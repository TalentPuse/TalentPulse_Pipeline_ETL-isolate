"""CareerViet detail crawler — plain HTTP, no browser.

Structurally the same loop as the ITviec crawler, minus Playwright: CareerViet
serves complete HTML to a plain client, so this reuses the VietnamWorks
`Fetcher` (status-aware retry, 403/404/429/5xx backoff, Retry-After, block
keyword detection) instead of driving Chromium.

Importing `Fetcher` across source packages is a wart. It is generic — nothing in
it is VietnamWorks-specific except the default headers — and duplicating it
would be worse. Moving it (and the typed exceptions) to a shared
`src/crawlers/fetcher.py` is tracked as separate work.
"""
from __future__ import annotations

import argparse
import gzip
import logging
import time
import uuid

from src.crawlers.vietnamworks.detail.fetcher import (
    BlockedError,
    ExpiredError,
    Fetcher,
    TransientError,
)
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient
from src.utils import safety
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.config import config
from src.utils.rate_limiter import jitter_sleep

logger = logging.getLogger(__name__)

SOURCE = "careerviet"

# Detail pages are ~1.2 MB of server-rendered HTML. Gzip before upload or the
# raw bucket grows by more than a gigabyte per full crawl.
MIN_HTML_BYTES = 20_000


class CareerVietDetailCrawler:
    """Fetch CareerViet detail pages and store gzipped HTML in object storage."""

    def __init__(
        self,
        log: CrawlLog,
        minio: MinioClient,
        fetcher: Fetcher | None = None,
        run_id: str | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        self.log = log
        self.minio = minio
        self.fetcher = fetcher or Fetcher()
        self.bucket = config.S3_BUCKET_NAME
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.breaker = breaker or CircuitBreaker()

    def _object_key(self, job_id: str) -> str:
        return f"details/careerviet/html/{self.run_id}/{job_id}.html.gz"

    def process_one(self, job_id: str, url: str) -> str:
        """Fetch + store a single job. Returns the final status string."""
        try:
            status, html, latency_ms = self.fetcher.fetch(url)
        except ExpiredError:
            self.log.mark_expired(job_id, source=SOURCE)
            self.breaker.record(404)
            return "expired"
        except BlockedError as exc:
            logger.error(f"BLOCKED on {url}: {exc}. Triggering kill switch.")
            self.breaker.record(403)
            self.log.mark_failed(job_id, 403, str(exc), source=SOURCE)
            safety.trigger()
            raise
        except TransientError as exc:
            self.breaker.record(exc.status)
            self.log.mark_failed(job_id, exc.status, str(exc), source=SOURCE)
            return "failed"

        # A 200 that is too small is a redirect to a placeholder or an expired
        # posting rendered as a stub — storing it would produce a JSON row with
        # no JobPosting and a parse failure later.
        if len(html) < MIN_HTML_BYTES:
            logger.warning(f"Suspiciously small page for {url} ({len(html)}B)")
            self.breaker.record(status)
            self.log.mark_failed(job_id, status, f"page too small ({len(html)}B)", source=SOURCE)
            return "failed"

        self.breaker.record(status)
        key = self._object_key(job_id)
        payload = gzip.compress(html.encode("utf-8"))
        self.minio.upload_bytes(self.bucket, key, payload, "application/gzip")
        self.log.mark_success(job_id, status, key, self.run_id, source=SOURCE)
        logger.info(f"OK {job_id} ({latency_ms}ms, {len(payload)}B gz) -> {key}")
        return "success"

    def run(self, max_jobs: int | None = None) -> dict:
        counters = {"success": 0, "failed": 0, "expired": 0, "skipped": 0}

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
                logger.info("No more pending CareerViet jobs")
                break
            job_id, url = job

            try:
                outcome = self.process_one(job_id, url)
            except BlockedError:
                counters["failed"] += 1
                break
            except Exception as exc:
                # process_one handles the known failure modes; this catches the
                # unexpected (an upload error, say) so a claimed row is never
                # abandoned in in_progress.
                logger.error(f"process_one crashed for {job_id}: {exc}")
                self.breaker.record(None)
                self.log.mark_failed(job_id, None, str(exc), source=SOURCE)
                outcome = "failed"

            counters[outcome] = counters.get(outcome, 0) + 1
            processed += 1
            logger.info(
                f"[careerviet] {processed}/{max_jobs or '?'} {outcome}"
                f" — job_id={job_id} (ok={counters['success']} fail={counters['failed']})"
            )

            jitter_sleep(config.CRAWLER_RATE_SECONDS)

        logger.info(f"CareerViet detail run finished: {counters}")
        return counters


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-jobs", type=int, default=None)
    args = parser.parse_args()

    log = CrawlLog()
    try:
        CareerVietDetailCrawler(log=log, minio=MinioClient()).run(max_jobs=args.max_jobs)
    finally:
        log.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    main()
