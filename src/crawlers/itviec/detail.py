"""ITviec detail page crawler using Playwright stealth browser."""
from __future__ import annotations

import gzip
import logging
import time
import uuid

from src.crawlers.browser import StealthBrowser
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.rate_limiter import jitter_sleep
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient
from src.utils import safety
from src.utils.config import config

logger = logging.getLogger(__name__)

SOURCE = "itviec"


class ITviecDetailCrawler:
    """Fetch ITviec detail pages via Playwright and store HTML in MinIO."""

    def __init__(
        self,
        browser: StealthBrowser,
        log: CrawlLog,
        minio: MinioClient,
        run_id: str | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        self.browser = browser
        self.log = log
        self.minio = minio
        self.bucket = config.S3_BUCKET_NAME
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.breaker = breaker or CircuitBreaker()

    def _object_key(self, job_id: str) -> str:
        return f"details/itviec/html/{self.run_id}/{job_id}.html.gz"

    def process_one(self, job_id: str, url: str) -> str:
        """Fetch + store a single job. Returns final status string."""
        try:
            html = self.browser.fetch_page(url)
        except Exception as e:
            logger.error(f"Fetch failed for {url}: {e}")
            self.breaker.record(None)
            self.log.mark_failed(job_id, None, str(e), source=SOURCE)
            return "failed"

        if not html or len(html) < 1000:
            logger.warning(f"Empty/tiny response for {url} ({len(html or '')} chars)")
            self.breaker.record(None)
            self.log.mark_failed(job_id, None, "empty response", source=SOURCE)
            return "failed"

        # Check for Cloudflare block indicators
        lowered = html[:4000].lower()
        if "attention required" in lowered or "access denied" in lowered:
            logger.error(f"BLOCKED on {url}")
            self.breaker.record(403)
            self.log.mark_failed(job_id, 403, "cloudflare block detected", source=SOURCE)
            safety.trigger()
            return "failed"

        self.breaker.record(200)
        key = self._object_key(job_id)
        payload = gzip.compress(html.encode("utf-8"))
        self.minio.upload_bytes(self.bucket, key, payload, "application/gzip")
        self.log.mark_success(job_id, 200, key, self.run_id, source=SOURCE)
        logger.info(f"OK {job_id} ({len(payload)}B gz) -> {key}")
        return "success"

    def run(self, max_jobs: int | None = None) -> dict:
        """Process pending ITviec jobs from crawl_log queue."""
        counters = {"success": 0, "failed": 0, "skipped": 0}

        if safety.is_killed():
            logger.warning("Kill switch active before start, aborting")
            return counters

        processed = 0
        while True:
            if max_jobs is not None and processed >= max_jobs:
                break
            if safety.is_killed():
                logger.warning("Kill switch active, stopping")
                break
            if self.breaker.is_open():
                logger.warning(f"Breaker open ({self.breaker.reason()}), stopping")
                break

            job = self.log.claim_next(source=SOURCE)
            if job is None:
                logger.info("No more pending ITviec jobs")
                break
            job_id, url = job

            try:
                outcome = self.process_one(job_id, url)
            except Exception as e:
                # process_one already handles known failure modes internally;
                # this catches anything unexpected (e.g. minio.upload_bytes
                # errors) so the claimed row never stays stuck in_progress.
                logger.error(f"process_one crashed for {job_id}: {e}")
                self.breaker.record(None)
                self.log.mark_failed(job_id, None, str(e), source=SOURCE)
                outcome = "failed"
            counters[outcome] = counters.get(outcome, 0) + 1
            processed += 1
            logger.info(
                f"[itviec] {processed}/{max_jobs or '?'} {outcome}"
                f" — job_id={job_id} (ok={counters['success']} fail={counters['failed']})"
            )

            jitter_sleep(config.CRAWLER_RATE_SECONDS)

        logger.info(f"ITviec detail run finished: {counters}")
        return counters
