"""ITviec listing page crawler using Playwright stealth browser."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from src.crawlers.browser import StealthBrowser
from src.storage.minio_client import MinioClient
from src.utils.config import config

logger = logging.getLogger(__name__)

ITVIEC_BASE = "https://itviec.com"
MAX_CONSECUTIVE_EMPTY = 3


def extract_json_ld_urls(html: str) -> list[str]:
    """Extract job detail URLs from JSON-LD ItemList in listing HTML."""
    urls: list[str] = []
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("@type") != "ItemList":
            continue
        for item in data.get("itemListElement", []):
            url = item.get("url")
            if url and "/it-jobs/" in url:
                urls.append(url)
    return urls


def detect_max_page(html: str) -> int:
    """Parse pagination links to find the last page number."""
    max_page = 1
    for match in re.finditer(r'href="[^"]*[?&]page=(\d+)', html):
        try:
            page_num = int(match.group(1))
            max_page = max(max_page, page_num)
        except ValueError:
            continue
    return max_page


class ITviecListingCrawler:
    """Crawl ITviec listing pages, extract job URLs via JSON-LD."""

    def __init__(
        self,
        browser: StealthBrowser,
        minio: MinioClient | None = None,
    ):
        self.browser = browser
        self.minio = minio or MinioClient()
        self.bucket = config.S3_BUCKET_NAME

    def _upload_html(self, html: str, keyword: str, page: int) -> None:
        slug = keyword.replace(" ", "_") or "nokeyword"
        object_name = f"listings/itviec/list_{slug}_p{page}_{int(time.time())}.html"
        self.minio.upload_string(
            bucket_name=self.bucket,
            object_name=object_name,
            content=html,
            content_type="text/html",
        )
        logger.info(f"Uploaded listing: {object_name}")

    def crawl_keyword(
        self,
        keyword: str,
        max_pages: Optional[int] = None,
    ) -> list[str]:
        """Crawl all pages for one keyword slug. Returns list of detail URLs."""
        all_urls: list[str] = []
        page = 1
        page_cap = max_pages or 99
        consecutive_empty = 0

        while page <= page_cap:
            url = f"{ITVIEC_BASE}/it-jobs/{keyword}"
            if page > 1:
                url += f"?page={page}"

            logger.info(f"Fetching listing: {url}")
            try:
                html = self.browser.fetch_page(url)
            except Exception as e:
                logger.error(f"Failed to fetch {url}: {e}")
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    logger.warning("Circuit breaker: too many consecutive failures")
                    break
                page += 1
                continue

            urls = extract_json_ld_urls(html)

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
            time.sleep(2 + 2 * __import__("random").random())

        return all_urls

    def crawl_all_listings(
        self,
        keywords: list[str] | None = None,
        max_pages: Optional[int] = None,
    ) -> dict:
        """Crawl all keywords. Returns counters + collected URLs."""
        keywords = keywords or config.ITVIEC_KEYWORDS
        all_urls: list[str] = []
        counters = {"keywords": 0, "pages": 0, "jobs_raw": 0}

        for kw in keywords:
            urls = self.crawl_keyword(kw, max_pages=max_pages)
            counters["keywords"] += 1
            counters["jobs_raw"] += len(urls)
            all_urls.extend(urls)
            logger.info(
                f"[itviec-listing] keyword '{kw}' done: {len(urls)} URLs"
                f" ({counters['keywords']}/{len(keywords)} keywords)"
            )

        unique = list(dict.fromkeys(all_urls))
        counters["jobs_unique"] = len(unique)
        logger.info(f"Listing crawl done: {counters}")
        return {"counters": counters, "urls": unique}
