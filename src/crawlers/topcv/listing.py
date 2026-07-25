"""TopCV listing page crawler using Playwright stealth browser.

TopCV listing pages do NOT expose an ItemList JSON-LD (unlike ITviec), so detail
URLs are extracted from the raw HTML by regex.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional

from src.crawlers.browser import StealthBrowser
from src.storage.minio_client import MinioClient
from src.utils.config import config

logger = logging.getLogger(__name__)

TOPCV_BASE = "https://www.topcv.vn"
MAX_CONSECUTIVE_EMPTY = 3
# TopCV serves a ~27KB Cloudflare challenge instead of the ~1.6MB real page on
# some fetches (much more often from datacenter IPs like CI runners). Treat any
# response below this size as a challenge and retry, otherwise a challenged
# first fetch yields 0 URLs -> 0 seeded -> the whole pipeline runs empty.
CHALLENGE_MIN_LEN = 60_000
FETCH_RETRIES = 4
# Budget for a Cloudflare managed-challenge page to auto-solve in a real browser
# (its JS runs, gets cf_clearance, then loads the real page). Content-ready
# polling returns early when the real page arrives, so this is just the cap.
CHALLENGE_WAIT_MS = 20_000

# Matches a TopCV detail link, capturing everything up to `.html` (query stripped).
_DETAIL_RE = re.compile(r"https://www\.topcv\.vn/viec-lam/[\w\-]+/\d+\.html")
_PAGE_RE = re.compile(r"[?&]page=(\d+)")


def extract_detail_urls(html: str) -> list[str]:
    """Extract deduped, query-stripped job detail URLs from listing HTML."""
    urls = _DETAIL_RE.findall(html)
    return list(dict.fromkeys(urls))


def detect_max_page(html: str) -> int:
    """Parse pagination links to find the last page number."""
    max_page = 1
    for m in _PAGE_RE.finditer(html):
        try:
            max_page = max(max_page, int(m.group(1)))
        except ValueError:
            continue
    return max_page


class TopCVListingCrawler:
    """Crawl TopCV listing pages, extract job URLs via regex."""

    def __init__(self, browser: StealthBrowser, minio: MinioClient | None = None):
        self.browser = browser
        self.minio = minio or MinioClient()
        self.bucket = config.S3_BUCKET_NAME

    def _upload_html(self, html: str, keyword: str, page: int) -> None:
        slug = keyword.replace(" ", "_") or "nokeyword"
        object_name = f"listings/topcv/list_{slug}_p{page}_{int(time.time())}.html"
        self.minio.upload_string(
            bucket_name=self.bucket,
            object_name=object_name,
            content=html,
            content_type="text/html",
        )
        logger.info(f"Uploaded listing: {object_name}")

    def crawl_keyword(self, keyword: str, max_pages: Optional[int] = None) -> list[str]:
        """Crawl all pages for one keyword slug. Returns list of detail URLs."""
        all_urls: list[str] = []
        page = 1
        page_cap = max_pages or 99
        consecutive_empty = 0

        while page <= page_cap:
            url = f"{TOPCV_BASE}/tim-viec-lam-{keyword}"
            if page > 1:
                url += f"?page={page}"

            logger.info(f"Fetching listing: {url}")
            try:
                html = self.browser.fetch_page(
                    url,
                    wait_ms=CHALLENGE_WAIT_MS,
                    retries=FETCH_RETRIES,
                    min_len=CHALLENGE_MIN_LEN,
                    persist_cookies=True,
                )
            except Exception as e:
                logger.error(f"Failed to fetch {url}: {e}")
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    logger.warning("Circuit breaker: too many consecutive failures")
                    break
                page += 1
                continue

            urls = extract_detail_urls(html)

            if not urls:
                consecutive_empty += 1
                logger.warning(f"Empty page {page} for '{keyword}' (fail #{consecutive_empty})")
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    logger.warning("Circuit breaker: stopping keyword")
                    break
                page += 1
                continue

            consecutive_empty = 0
            all_urls.extend(urls)
            logger.info(f"[{keyword}] page {page}: {len(urls)} jobs")

            try:
                self._upload_html(html, keyword, page)
            except Exception as e:
                logger.error(f"MinIO upload failed for {keyword} p{page}: {e}")

            if page == 1:
                detected_max = detect_max_page(html)
                page_cap = min(max_pages or detected_max, detected_max)
                logger.info(f"[{keyword}] detected {detected_max} pages, cap={page_cap}")

            if page >= page_cap:
                break

            page += 1
            time.sleep(2 + 2 * random.random())

        return all_urls

    def crawl_all_listings(
        self,
        keywords: list[str] | None = None,
        max_pages: Optional[int] = None,
    ) -> dict:
        """Crawl all keywords. Returns counters + collected URLs."""
        keywords = keywords or config.TOPCV_KEYWORDS
        all_urls: list[str] = []
        counters = {"keywords": 0, "pages": 0, "jobs_raw": 0}

        for kw in keywords:
            urls = self.crawl_keyword(kw, max_pages=max_pages)
            counters["keywords"] += 1
            counters["jobs_raw"] += len(urls)
            all_urls.extend(urls)
            logger.info(
                f"[topcv-listing] keyword '{kw}' done: {len(urls)} URLs"
                f" ({counters['keywords']}/{len(keywords)} keywords)"
            )

        unique = list(dict.fromkeys(all_urls))
        counters["jobs_unique"] = len(unique)
        logger.info(f"Listing crawl done: {counters}")
        return {"counters": counters, "urls": unique}
