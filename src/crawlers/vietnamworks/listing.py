import json
import logging
import time
from typing import Optional

import requests

from src.utils.config import config
from src.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)


RETRIEVE_FIELDS = [
    "address", "benefits", "jobTitle", "salaryMax", "isSalaryVisible",
    "jobLevelVI", "isShowLogo", "salaryMin", "companyLogo", "userId",
    "jobLevel", "jobLevelId", "jobId", "jobUrl", "companyId", "approvedOn",
    "isAnonymous", "alias", "expiredOn", "industries", "industriesV3",
    "workingLocations", "services", "companyName", "salary", "onlineOn",
    "simpleServices", "visibilityDisplay", "isShowLogoInSearch",
    "priorityOrder", "skills", "profilePublishedSiteMask",
    # v2: include jobFunctionsV3 so downstream can verify focus
    "jobFunctionsV3", "groupJobFunctionsV3",
]


class VietnamWorksListingCrawler:
    """Crawler for VietnamWorks job listings via ms.vietnamworks.com search API.

    v2 changes:
    - Supports server-side filtering by jobFunctionV3Id (verified to work: 50/50
      in-focus when filter applied vs 47/50 when keyword-only).
    - Auto-paginates up to `nbPages` from API response meta, capped by
      config.LISTING_MAX_PAGES to avoid runaway crawls.
    """

    SEARCH_ENDPOINT = "https://ms.vietnamworks.com/job-search/v1.0/search"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.minio_client = MinioClient()
        self.bucket_name = config.S3_BUCKET_NAME

    def build_payload(
        self,
        keyword: str,
        location_id: int = 29,
        page: int = 0,
        job_function_ids: Optional[list[int]] = None,
    ) -> dict:
        """Build the JSON payload for the search API. Public for testability."""
        filters: list[dict] = [
            {"field": "workingLocations.cityId", "value": str(location_id)}
        ]
        for fid in (job_function_ids or []):
            filters.append(
                {"field": "jobFunctionsV3.jobFunctionV3Id", "value": str(fid)}
            )
        return {
            "userId": 0,
            "query": keyword,
            "filter": filters,
            "ranges": [],
            "order": [],
            "hitsPerPage": 50,
            "page": page,
            "retrieveFields": list(RETRIEVE_FIELDS),
        }

    def fetch_search_page(
        self,
        keyword: str,
        location_id: int = 29,
        page: int = 0,
        job_function_ids: Optional[list[int]] = None,
    ) -> Optional[dict]:
        """Fetch one search page. Pagination is 0-indexed."""
        payload = self.build_payload(
            keyword=keyword,
            location_id=location_id,
            page=page,
            job_function_ids=job_function_ids,
        )
        try:
            logger.info(
                f"Fetching keyword='{keyword}' page={page} "
                f"functions={job_function_ids or []}..."
            )
            response = self.session.post(self.SEARCH_ENDPOINT, json=payload, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch keyword='{keyword}' page={page}: {e}")
            return None

    def _upload_json(self, json_data: dict, keyword: str, page: int) -> bool:
        if "data" not in json_data or not json_data["data"]:
            logger.warning("No job data found in JSON response")
            return False
        try:
            slug = keyword.replace(" ", "_") or "nokeyword"
            object_name = (
                f"listings/vietnamworks/list_{slug}_p{page}_{int(time.time())}.json"
            )
            self.minio_client.upload_string(
                bucket_name=self.bucket_name,
                object_name=object_name,
                content=json.dumps(json_data, ensure_ascii=False),
                content_type="application/json",
            )
            logger.info(f"Uploaded listing to MinIO: {object_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload JSON: {e}")
            return False

    def crawl_all_listings(
        self,
        keyword: str,
        max_pages: Optional[int] = None,
        job_function_ids: Optional[list[int]] = None,
    ) -> dict:
        """Iterate pages until reaching nbPages or cap. Returns counters."""
        cap = max_pages or config.LISTING_MAX_PAGES
        counters = {"pages": 0, "jobs": 0, "failures": 0}

        page = 0
        while page < cap:
            data = self.fetch_search_page(
                keyword=keyword,
                page=page,
                job_function_ids=job_function_ids,
            )
            if not data:
                counters["failures"] += 1
                logger.warning(f"Stopping '{keyword}' due to fetch failure on page {page}")
                break

            if not self._upload_json(data, keyword, page):
                break

            jobs_found = len(data.get("data", []) or [])
            counters["pages"] += 1
            counters["jobs"] += jobs_found

            nb_pages = int((data.get("meta") or {}).get("nbPages", 1) or 1)
            page += 1
            if page >= nb_pages:
                logger.info(
                    f"Completed '{keyword}': {page}/{nb_pages} pages, "
                    f"{counters['jobs']} jobs"
                )
                break

            time.sleep(2)  # polite delay between pages

        return counters


if __name__ == "__main__":
    crawler = VietnamWorksListingCrawler()
    # Focus-first: function filter alone is enough to skip 100% of noise
    # (verified against live API: 50/50 in-focus when filter applied).
    crawler.crawl_all_listings(
        keyword="",
        job_function_ids=config.TARGET_JOB_FUNCTION_IDS,
    )
