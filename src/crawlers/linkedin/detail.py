"""LinkedIn detail page crawler — requests-based, no browser needed."""
from __future__ import annotations

import gzip
import logging
import time
import uuid

from src.crawlers.linkedin.fetcher import BlockedError, Fetcher, TransientError
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.rate_limiter import TokenBucket, jitter_sleep
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient
from src.utils import safety
from src.utils.config import config

logger = logging.getLogger(__name__)

SOURCE = "linkedin"
DETAIL_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


class LinkedInDetailCrawler:
    def __init__(
        self,
        log: CrawlLog,
        fetcher: Fetcher | None = None,
        minio: MinioClient | None = None,
        run_id: str | None = None,
        rate_limiter: TokenBucket | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        self.log = log
        self.fetcher = fetcher or Fetcher()
        self.minio = minio or MinioClient()
        self.bucket = config.S3_BUCKET_NAME
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        rate = 1.0 / config.LINKEDIN_RATE_SECONDS
        self.rate_limiter = rate_limiter or TokenBucket(rate_per_sec=rate, burst=1)
        self.breaker = breaker or CircuitBreaker()

    def _object_key(self, job_id: str) -> str:
        return f"details/linkedin/html/{self.run_id}/{job_id}.html.gz"

    def process_one(self, job_id: str, url: str) -> str:
        self.rate_limiter.acquire()
        api_url = DETAIL_API.format(job_id=job_id)
        try:
            status, html, latency_ms = self.fetcher.fetch(api_url)
        except BlockedError as e:
            logger.error(f"BLOCKED on {api_url}: {e}")
            self.breaker.record(403)
            self.log.mark_failed(job_id, 403, str(e), source=SOURCE)
            safety.trigger()
            raise
        except TransientError as e:
            self.breaker.record(e.status)
            self.log.mark_failed(job_id, e.status, str(e), source=SOURCE)
            return "failed"

        if not html or len(html) < 500:
            self.breaker.record(None)
            self.log.mark_failed(job_id, None, "empty response", source=SOURCE)
            return "failed"

        self.breaker.record(status)
        key = self._object_key(job_id)
        payload = gzip.compress(html.encode("utf-8"))
        self.minio.upload_bytes(self.bucket, key, payload, "application/gzip")
        self.log.mark_success(job_id, status, key, self.run_id, source=SOURCE)
        logger.info(f"OK {job_id} ({latency_ms}ms, {len(payload)}B gz) -> {key}")
        return "success"

    def run(self, max_jobs: int | None = None) -> dict:
        counters = {"success": 0, "failed": 0, "skipped": 0}

        if safety.is_killed():
            logger.warning("kill switch active before start, aborting")
            return counters

        processed = 0
        while True:
            if max_jobs is not None and processed >= max_jobs:
                break
            if safety.is_killed():
                logger.warning("kill switch active, stopping")
                break
            if self.breaker.is_open():
                logger.warning(f"breaker open ({self.breaker.reason()}), stopping")
                break

            job = self.log.claim_next(source=SOURCE)
            if job is None:
                logger.info("no more pending LinkedIn jobs")
                break
            job_id, url = job

            try:
                outcome = self.process_one(job_id, url)
            except BlockedError:
                counters["failed"] += 1
                break
            counters[outcome] = counters.get(outcome, 0) + 1
            processed += 1
            logger.info(
                f"[linkedin] {processed}/{max_jobs or '?'} {outcome}"
                f" — job_id={job_id} (ok={counters['success']} fail={counters['failed']})"
            )

            jitter_sleep(config.LINKEDIN_RATE_SECONDS)

        logger.info(f"LinkedIn detail run finished: {counters}")
        return counters
