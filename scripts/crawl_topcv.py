#!/usr/bin/env python
"""Fast standalone TopCV crawler — RUN FROM A RESIDENTIAL IP (home PC).

TopCV's Cloudflare hard-blocks datacenter IPs (VPS / CI runners return 403),
so this must run from a residential connection. It:

  1. Solves the Cloudflare challenge ONCE with a real browser (StealthBrowser),
     grabbing the cf_clearance / bot-management cookies + the exact User-Agent.
  2. Fetches every listing + detail page with curl_cffi (real Chrome TLS
     impersonation) carrying those cookies — ~0.5s/page, in parallel — instead
     of driving the browser per page (~8s/page).
  3. Archives each detail HTML.gz to R2 and loads parsed rows into
     raw.job_detail via a SINGLE batched connection (upsert_many), so it never
     opens a connection-per-row storm against the warehouse (max_connections
     is low).

Normalize + dbt are intentionally NOT run here — run them on the VPS where the
DB is local (see the note printed at the end).

Setup (home PC, once):
    .venv\\Scripts\\python.exe -m pip install curl_cffi
    playwright install chromium     # if not already

Run (from the pipeline_data repo root):
    .venv\\Scripts\\python.exe scripts/crawl_topcv.py
    .venv\\Scripts\\python.exe scripts/crawl_topcv.py --keywords data-engineer,ai-engineer --workers 4

Env (from .env or shell): S3_* (R2), DB_* (warehouse over tailnet),
TOPCV_KEYWORDS. SKIP_FOCUS_SOURCES must include 'topcv' (it does by default).
"""
from __future__ import annotations

import argparse
import gzip
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure repo root on path when run as `python scripts/crawl_topcv.py`
sys.path.insert(0, ".")

from curl_cffi import requests as creq  # noqa: E402

from src.crawlers.browser import StealthBrowser  # noqa: E402
from src.crawlers.topcv.listing import (  # noqa: E402
    extract_detail_urls,
    detect_max_page,
    TOPCV_BASE,
)
from src.parsers.topcv.detail_parser import TopCVDetailParser, TopCVParseError  # noqa: E402
from src.queue.topcv_seeder import extract_job_id  # noqa: E402
from src.loaders.validators import validate  # noqa: E402
from src.storage.job_detail_repo import JobDetailRepo  # noqa: E402
from src.storage.minio_client import MinioClient  # noqa: E402
from src.utils.config import config  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crawl_topcv")

CHALLENGE_MIN_LEN = 60_000  # real page ~1.6MB; CF challenge ~5-27KB
JOB_RE = re.compile(r"https://www\.topcv\.vn/viec-lam/[\w\-]+/\d+\.html")


def solve_cloudflare() -> tuple[dict, str, str]:
    """Open a real browser once to pass Cloudflare; return (cookies, ua, page1_html)."""
    log.info("Solving Cloudflare with a real browser (once)...")
    with StealthBrowser() as b:
        html = b.fetch_page(
            f"{TOPCV_BASE}/tim-viec-lam-{config.TOPCV_KEYWORDS[0]}",
            wait_ms=25_000,
            retries=3,
            min_len=CHALLENGE_MIN_LEN,
            persist_cookies=True,
        )
        if len(html) < CHALLENGE_MIN_LEN:
            raise SystemExit(
                "Could not pass Cloudflare — this IP may be temporarily rate-flagged "
                "(crawled too much recently) or is a datacenter IP. Wait a few hours "
                "or run from a different residential connection."
            )
        cookies = {c["name"]: c["value"] for c in b._ctx.cookies()}
        ua = b._page.evaluate("() => navigator.userAgent")
    log.info("Cloudflare solved. cookies=%d", len(cookies))
    return cookies, ua, html


class FastFetcher:
    """curl_cffi session per thread, sharing the browser-solved cookies + UA."""

    def __init__(self, cookies: dict, ua: str, tries: int = 6):
        self._cookies = cookies
        self._ua = ua
        self._tries = tries
        self._local = threading.local()

    def _session(self) -> creq.Session:
        if not hasattr(self._local, "s"):
            self._local.s = creq.Session(
                impersonate="chrome",
                headers={"User-Agent": self._ua, "Accept-Language": "vi,en;q=0.9"},
                cookies=self._cookies,
            )
        return self._local.s

    def get(self, url: str) -> str | None:
        """Fetch a real page; retry on 403/429/short (rate limit) with backoff."""
        for i in range(self._tries):
            try:
                r = self._session().get(url, timeout=25)
                if r.status_code == 200 and len(r.text) >= CHALLENGE_MIN_LEN:
                    return r.text
            except Exception:
                pass
            time.sleep(1.5 + i * 0.8)  # backoff — TopCV rate-limits per IP
        return None


def collect_urls(fetcher: FastFetcher, keywords: list[str], page1_html: str,
                 max_pages: int) -> list[str]:
    """Fetch listing pages (parallel across keywords) and extract detail URLs."""
    def per_keyword(kw: str) -> set[str]:
        first = page1_html if kw == config.TOPCV_KEYWORDS[0] else fetcher.get(
            f"{TOPCV_BASE}/tim-viec-lam-{kw}"
        )
        if not first:
            log.warning("listing '%s' page 1 failed", kw)
            return set()
        urls = set(extract_detail_urls(first))
        cap = min(detect_max_page(first), max_pages)
        for p in range(2, cap + 1):
            hp = fetcher.get(f"{TOPCV_BASE}/tim-viec-lam-{kw}?page={p}")
            if hp:
                urls |= set(extract_detail_urls(hp))
        log.info("listing '%s': %d urls (%d pages)", kw, len(urls), cap)
        return urls

    all_urls: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(3, len(keywords))) as pool:
        for s in pool.map(per_keyword, keywords):
            all_urls |= s
    return list(all_urls)


def crawl_details(fetcher: FastFetcher, urls: list[str], minio: MinioClient,
                  run_id: str, workers: int):
    """Parallel-fetch every detail page; archive HTML.gz to R2. Returns list of
    (job_id, url, html-or-None)."""
    bucket = config.S3_BUCKET_NAME

    def one(url: str):
        jid = extract_job_id(url)
        html = fetcher.get(url)
        if html:
            key = f"details/topcv/html/{run_id}/{jid}.html.gz"
            minio.upload_bytes(bucket, key, gzip.compress(html.encode("utf-8")),
                               "application/gzip")
        return (jid, url, html)

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, u) for u in urls]
        for f in as_completed(futs):
            results.append(f.result())
            done += 1
            if done % 25 == 0:
                log.info("fetched %d/%d", done, len(urls))
    return results


def parse_and_load(results, workers: int) -> dict:
    """Parse HTML in memory (parallel) -> validate -> ONE batched upsert."""
    parser = TopCVDetailParser.__new__(TopCVDetailParser)  # no MinIO needed for parse_html

    def parse_one(item):
        jid, url, html = item
        if not html:
            return None
        try:
            detail = TopCVDetailParser.parse_html(parser, html, source_job_id=jid)
            return detail.to_dict()
        except TopCVParseError:
            return None

    payloads = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for p in pool.map(parse_one, results):
            if p:
                payloads.append(p)

    # validate (topcv skips focus via SKIP_FOCUS_SOURCES; business rules still apply)
    valid, rejected = [], 0
    for p in payloads:
        if validate(p) is None:
            valid.append(p)
        else:
            rejected += 1

    loaded = JobDetailRepo().upsert_many(valid) if valid else 0  # SINGLE connection
    return {"parsed": len(payloads), "valid": len(valid), "rejected": rejected,
            "loaded": loaded}


def main() -> None:
    ap = argparse.ArgumentParser(description="Fast TopCV crawler (residential IP only)")
    ap.add_argument("--keywords", default=",".join(config.TOPCV_KEYWORDS),
                    help="comma-separated keyword slugs")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel detail fetchers (4-5 avoids TopCV's per-IP 429)")
    ap.add_argument("--max-pages", type=int, default=5, help="max listing pages per keyword")
    args = ap.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if "topcv" not in config.SKIP_FOCUS_SOURCES:
        log.warning("topcv not in SKIP_FOCUS_SOURCES — jobs may be rejected. "
                    "Set SKIP_FOCUS_SOURCES=itviec,topcv")

    t0 = time.time()
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_fast"
    cookies, ua, page1 = solve_cloudflare()
    fetcher = FastFetcher(cookies, ua)

    urls = collect_urls(fetcher, keywords, page1, args.max_pages)
    log.info("LISTING: %d unique detail urls (%.0fs)", len(urls), time.time() - t0)
    if not urls:
        raise SystemExit("0 URLs — aborting (IP flagged? try again later).")

    minio = MinioClient()
    results = crawl_details(fetcher, urls, minio, run_id, args.workers)
    ok = sum(1 for _, _, h in results if h)
    log.info("FETCH: %d/%d ok (%.0fs)", ok, len(results), time.time() - t0)

    stats = parse_and_load(results, args.workers)
    log.info("PARSE/LOAD: %s (%.0fs)", stats, time.time() - t0)

    print("\n" + "=" * 60)
    print(f"DONE in {time.time()-t0:.0f}s — {stats['loaded']} topcv rows in raw.job_detail")
    print("Next: on the VPS, run normalize + dbt to publish to gold, e.g.")
    print("  docker exec <topcv-worker> python -c \"from src.normalizer.runner import "
          "NormalizerRunner; print(NormalizerRunner().run())\"")
    print("  then: dbt run --select +fct_jobs_daily  (incremental)")
    print("=" * 60)


if __name__ == "__main__":
    main()
