"""CareerViet listing crawler — plain HTTP, no browser.

CareerViet is the cheapest source in the warehouse: the site is server-rendered
behind nginx/openresty with no Cloudflare, so a plain `requests` GET is enough.
No Playwright, no Chromium, which is why this runs on the standard `worker`
image instead of needing its own.

Pagination was the one open question. The page reports 11,247 results for a
broad keyword but exposes only ~55 links and no pagination `href` — the pager is
JS. Probing the URL space found the server-rendered form:

    page 1 : /viec-lam/{slug}-k-vi.html
    page N : /viec-lam/{slug}-k-trang-{N}-vi.html

Verified the second form keeps the same query rather than starting a new one:
the reported total (11,247) and the `<title>` stay identical across pages, and
page 2 returned 46 links unseen on page 1, page 3 another 51.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import requests

from src.storage.minio_client import MinioClient
from src.utils.config import config

logger = logging.getLogger(__name__)

CAREERVIET_BASE = "https://careerviet.vn"

# Detail links look like /vi/tim-viec-lam/{slug}.{HEXID}.html
DETAIL_HREF_RE = re.compile(r'href="(/vi/tim-viec-lam/[^"]+\.html)"')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Sponsored postings are pinned onto every page, so consecutive pages always
# overlap. Stop when a page contributes nothing new rather than when it is
# empty — an "empty" page never arrives, the same pinned block does.
MAX_CONSECUTIVE_NO_NEW = 2


def extract_detail_urls(html: str) -> list[str]:
    """Pull absolute detail URLs out of a listing page, order preserved."""
    seen: dict[str, None] = {}
    for path in DETAIL_HREF_RE.findall(html):
        seen.setdefault(CAREERVIET_BASE + path, None)
    return list(seen)


def build_listing_url(keyword_slug: str, page: int) -> str:
    """Page 1 and page N use different URL shapes — see module docstring."""
    if page <= 1:
        return f"{CAREERVIET_BASE}/viec-lam/{keyword_slug}-k-vi.html"
    return f"{CAREERVIET_BASE}/viec-lam/{keyword_slug}-k-trang-{page}-vi.html"


class CareerVietListingCrawler:
    """Walk CareerViet search pages and collect detail URLs."""

    def __init__(
        self,
        session: requests.Session | None = None,
        minio: MinioClient | None = None,
    ):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.minio = minio or MinioClient()
        self.bucket = config.S3_BUCKET_NAME

    def _upload_html(self, html: str, keyword: str, page: int) -> None:
        object_name = f"listings/careerviet/list_{keyword}_p{page}_{int(time.time())}.html"
        self.minio.upload_string(
            bucket_name=self.bucket,
            object_name=object_name,
            content=html,
            content_type="text/html",
        )
        logger.info(f"Uploaded listing: {object_name}")

    def crawl_keyword(self, keyword_slug: str, max_pages: Optional[int] = None) -> list[str]:
        """Crawl pages for one keyword slug. Returns deduped detail URLs."""
        cap = max_pages or config.LISTING_MAX_PAGES
        collected: dict[str, None] = {}
        no_new_streak = 0

        for page in range(1, cap + 1):
            url = build_listing_url(keyword_slug, page)
            try:
                resp = self.session.get(url, timeout=config.CRAWLER_REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.warning(f"[careerviet] {keyword_slug} p{page} fetch failed: {exc}")
                break

            urls = extract_detail_urls(resp.text)
            fresh = [u for u in urls if u not in collected]
            for u in fresh:
                collected[u] = None

            logger.info(
                f"[careerviet] {keyword_slug} p{page}: {len(urls)} links, {len(fresh)} new "
                f"(running total {len(collected)})"
            )

            try:
                self._upload_html(resp.text, keyword_slug, page)
            except Exception as exc:
                # A failed archive upload must not lose the URLs we already have.
                logger.error(f"MinIO upload failed for {keyword_slug} p{page}: {exc}")

            if not fresh:
                no_new_streak += 1
                if no_new_streak >= MAX_CONSECUTIVE_NO_NEW:
                    logger.info(f"[careerviet] {keyword_slug}: no new links, stopping at p{page}")
                    break
            else:
                no_new_streak = 0

            time.sleep(config.CRAWLER_RATE_SECONDS)

        return list(collected)

    def crawl_all_listings(
        self,
        keywords: list[str] | None = None,
        max_pages: Optional[int] = None,
    ) -> dict:
        """Crawl every keyword. Returns counters plus the deduped URL list."""
        keywords = keywords or config.CAREERVIET_KEYWORDS
        all_urls: list[str] = []
        counters = {"keywords": 0, "jobs_raw": 0}

        for kw in keywords:
            urls = self.crawl_keyword(kw, max_pages=max_pages)
            counters["keywords"] += 1
            counters["jobs_raw"] += len(urls)
            all_urls.extend(urls)
            logger.info(
                f"[careerviet-listing] '{kw}' done: {len(urls)} URLs "
                f"({counters['keywords']}/{len(keywords)} keywords)"
            )

        unique = list(dict.fromkeys(all_urls))
        counters["jobs_unique"] = len(unique)
        logger.info(f"Listing crawl done: {counters}")
        return {"counters": counters, "urls": unique}
