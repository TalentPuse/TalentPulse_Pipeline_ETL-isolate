"""Orchestrates fetching VietnamWorks job detail pages."""
import argparse
import gzip
import logging
import time
import uuid

from src.crawlers.vietnamworks.detail.circuit_breaker import CircuitBreaker
from src.crawlers.vietnamworks.detail.fetcher import BlockedError, ExpiredError, Fetcher, TransientError
from src.crawlers.vietnamworks.detail.rate_limiter import TokenBucket, jitter_sleep
from src.crawlers.vietnamworks.detail.url_builder import is_allowed
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient
from src.utils import safety
from src.utils.config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class DetailCrawler:
    def __init__(
        self,
        log: CrawlLog,
        fetcher: Fetcher,
        minio: MinioClient,
        run_id: str | None = None,
        rate_limiter: TokenBucket | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        self.log = log
        self.fetcher = fetcher
        self.minio = minio
        self.bucket = config.S3_BUCKET_NAME
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.rate_limiter = rate_limiter or TokenBucket(
            rate_per_sec=1.0 / config.CRAWLER_RATE_SECONDS,
            burst=config.CRAWLER_BURST,
        )
        self.breaker = breaker or CircuitBreaker()

    def _object_key(self, job_id: str) -> str:
        return f"details/vietnamworks/html/{self.run_id}/{job_id}.html.gz"

    def process_one(self, job_id: str, url: str) -> str:
        """Fetch + store a single job. Returns final status string."""
        if not is_allowed(url):
            self.log.mark_failed(job_id, None, f"url not allowed: {url}")
            return "failed"

        self.rate_limiter.acquire()
        try:
            status, html, latency_ms = self.fetcher.fetch(url)
        except ExpiredError:
            self.log.mark_expired(job_id)
            self.breaker.record(404)
            return "expired"
        except BlockedError as e:
            logger.error(f"BLOCKED on {url}: {e}. Triggering kill switch.")
            self.breaker.record(403)
            self.log.mark_failed(job_id, 403, str(e))
            safety.trigger()
            raise
        except TransientError as e:
            self.breaker.record(e.status)
            self.log.mark_failed(job_id, e.status, str(e))
            return "failed"

        self.breaker.record(status)
        key = self._object_key(job_id)
        payload = gzip.compress(html.encode("utf-8"))
        self.minio.upload_bytes(self.bucket, key, payload, "application/gzip")
        self.log.mark_success(job_id, status, key, self.run_id)
        logger.info(f"OK {job_id} ({latency_ms}ms, {len(payload)}B gz) -> {key}")
        return "success"

    def run(self, max_jobs: int | None = None) -> dict:
        counters = {"success": 0, "failed": 0, "expired": 0, "skipped": 0}

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
                logger.warning(f"breaker open ({self.breaker.reason()}), sleeping 60s")
                time.sleep(60)
                continue

            job = self.log.claim_next()
            if job is None:
                logger.info("no more pending jobs, exiting")
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
                f"[vnw] {processed}/{max_jobs or '?'} {outcome}"
                f" — job_id={job_id} (ok={counters['success']} fail={counters['failed']})"
            )

            jitter_sleep(config.CRAWLER_RATE_SECONDS)

        logger.info(f"run finished: {counters}")
        return counters


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-jobs", type=int, default=None)
    args = parser.parse_args()

    log = CrawlLog()
    crawler = DetailCrawler(log=log, fetcher=Fetcher(), minio=MinioClient())
    crawler.run(max_jobs=args.max_jobs)


if __name__ == "__main__":
    main()
