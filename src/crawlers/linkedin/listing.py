"""LinkedIn listing crawler via public guest API."""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import requests

from src.crawlers.linkedin.user_agents import random_headers
from src.storage.minio_client import MinioClient
from src.utils.config import config

logger = logging.getLogger(__name__)

SEARCH_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
MAX_START = 975
JOBS_PER_PAGE = 25
MAX_CONSECUTIVE_EMPTY = 2


def _parse_job_ids(html: str) -> list[str]:
    """Extract job IDs from listing HTML fragments."""
    return re.findall(r'data-entity-urn="urn:li:jobPosting:(\d+)"', html)


class LinkedInListingCrawler:
    """Crawl LinkedIn public job listings for Vietnam."""

    def __init__(self, minio: MinioClient | None = None):
        self.session = requests.Session()
        self.minio = minio or MinioClient()
        self.bucket = config.S3_BUCKET_NAME
        self.geo_id = config.LINKEDIN_GEO_ID

    def _upload_html(self, html: str, keyword: str, start: int) -> None:
        slug = keyword.replace(" ", "_") or "nokeyword"
        object_name = f"listings/linkedin/list_{slug}_s{start}_{int(time.time())}.html"
        self.minio.upload_string(
            bucket_name=self.bucket,
            object_name=object_name,
            content=html,
            content_type="text/html",
        )

    def crawl_keyword(
        self,
        keyword: str,
        max_pages: Optional[int] = None,
    ) -> list[str]:
        """Crawl all pages for one keyword. Returns list of job IDs."""
        all_ids: list[str] = []
        seen: set[str] = set()
        consecutive_empty = 0
        page_cap = max_pages or (MAX_START // JOBS_PER_PAGE)

        page = 0
        while page < page_cap:
            start = page * JOBS_PER_PAGE
            if start > MAX_START:
                break

            params = {
                "keywords": keyword,
                "location": "Vietnam",
                "geoId": self.geo_id,
                "start": str(start),
            }
            self.session.headers.update(random_headers())

            try:
                resp = self.session.get(SEARCH_API, params=params, timeout=15)
                resp.raise_for_status()
                html = resp.text
            except requests.RequestException as e:
                logger.error(f"Listing fetch failed keyword='{keyword}' start={start}: {e}")
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    break
                page += 1
                time.sleep(3)
                continue

            ids = _parse_job_ids(html)
            new_ids = [jid for jid in ids if jid not in seen]
            seen.update(ids)
            all_ids.extend(new_ids)

            if not ids:
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    logger.info(f"[linkedin] '{keyword}' no more results at start={start}")
                    break
                page += 1
                time.sleep(2)
                continue

            consecutive_empty = 0
            logger.info(f"[linkedin] '{keyword}' start={start}: {len(ids)} jobs ({len(new_ids)} new), total={len(all_ids)}")

            try:
                self._upload_html(html, keyword, start)
            except Exception as e:
                logger.error(f"MinIO upload failed: {e}")

            page += 1
            time.sleep(2 + 1 * __import__("random").random())

        return all_ids

    def crawl_all_listings(
        self,
        keywords: list[str] | None = None,
        max_pages: Optional[int] = None,
    ) -> dict:
        """Crawl all keywords. Returns counters + all job IDs."""
        keywords = keywords or config.LINKEDIN_KEYWORDS
        all_ids: list[str] = []
        seen: set[str] = set()
        counters = {"keywords": 0, "jobs_raw": 0, "jobs_unique": 0}

        for kw in keywords:
            ids = self.crawl_keyword(kw, max_pages=max_pages)
            counters["keywords"] += 1
            counters["jobs_raw"] += len(ids)
            new = [jid for jid in ids if jid not in seen]
            seen.update(ids)
            all_ids.extend(new)
            logger.info(f"[linkedin] keyword '{kw}' done: {len(ids)} raw, {len(new)} new unique")
            time.sleep(3)

        counters["jobs_unique"] = len(all_ids)
        logger.info(f"LinkedIn listing crawl done: {counters}")
        return {"counters": counters, "job_ids": all_ids}
