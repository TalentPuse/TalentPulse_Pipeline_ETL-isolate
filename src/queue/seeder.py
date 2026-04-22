"""Seeder: read parsed listing JSONs from MinIO and enqueue into raw.crawl_log."""
import json
import logging

from src.crawlers.vietnamworks.detail.url_builder import is_allowed
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient
from src.utils.config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PARSED_PREFIX = "parsed/listings/vietnamworks/"


def seed_from_listings(prefix: str = PARSED_PREFIX) -> dict:
    log = CrawlLog()
    minio = MinioClient()
    bucket = config.S3_BUCKET_NAME

    counters = {"scanned_files": 0, "enqueued": 0, "skipped": 0, "rejected_url": 0}

    resp = minio.s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if not key.endswith(".json"):
            continue
        counters["scanned_files"] += 1

        body = minio.s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        records = json.loads(body)
        for rec in records:
            job_id = str(rec.get("source_job_id") or "").strip()
            url = (rec.get("source_url") or "").strip()
            if not job_id or not url:
                counters["skipped"] += 1
                continue
            if not is_allowed(url):
                counters["rejected_url"] += 1
                continue
            if log.enqueue(job_id, url):
                counters["enqueued"] += 1
            else:
                counters["skipped"] += 1

    logger.info(f"seed result: {counters}")
    return counters


if __name__ == "__main__":
    seed_from_listings()
