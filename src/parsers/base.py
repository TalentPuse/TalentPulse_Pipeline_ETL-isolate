"""Base class for parsers that read HTML.gz from MinIO and write parsed JSON."""
from __future__ import annotations

import gzip
import json
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed

from botocore.exceptions import ClientError

from src.parsers.vietnamworks.detail.schema import JobDetail
from src.storage.minio_client import MinioClient
from src.utils.config import config

logger = logging.getLogger(__name__)


class MinIOParser(ABC):
    """Template for MinIO-based HTML-to-JSON parsers.

    Subclasses only need to implement `parse_html()` and set
    `VERSION`, `HTML_PREFIX`, and `PARSED_PREFIX`.
    """

    VERSION: str
    HTML_PREFIX: str
    PARSED_PREFIX: str

    def __init__(self, minio: MinioClient | None = None):
        self.minio = minio or MinioClient()
        self.bucket = config.S3_BUCKET_NAME

    @abstractmethod
    def parse_html(self, html: str, source_job_id: str | None = None) -> JobDetail:
        ...

    def _extract_job_id(self, html_key: str) -> str:
        return html_key.split("/")[-1].replace(".html.gz", "")

    def _parsed_key(self, job_id: str) -> str:
        return f"{self.PARSED_PREFIX}{job_id}.json"

    def _exists(self, key: str) -> bool:
        try:
            self.minio.s3_client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def process_one(self, html_object_key: str, *, force: bool = False) -> str | None:
        """Parse one HTML.gz from MinIO, write JSON result. Returns parsed key or None."""
        job_id = self._extract_job_id(html_object_key)
        parsed_key = self._parsed_key(job_id)
        if not force and self._exists(parsed_key):
            return None

        body = self.minio.s3_client.get_object(Bucket=self.bucket, Key=html_object_key)["Body"].read()
        html = gzip.decompress(body).decode("utf-8", errors="replace")
        detail = self.parse_html(html, source_job_id=job_id)

        self.minio.upload_string(
            bucket_name=self.bucket,
            object_name=parsed_key,
            content=json.dumps(detail.to_dict(), ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        return parsed_key

    def run_batch(
        self, prefix: str | None = None, *, force: bool = False, max_workers: int = 16
    ) -> dict:
        """Parse all HTML.gz files under prefix, in parallel.

        Parsing each object is I/O-bound (two R2 round-trips: download the
        gzipped HTML, upload the parsed JSON) plus CPU-light HTML parsing, so a
        thread pool overlaps the network waits and cuts wall-clock roughly
        `max_workers`x. boto3's S3 client is thread-safe for concurrent calls and
        `parse_html` is pure, so no shared state is mutated across threads.
        """
        prefix = prefix or self.HTML_PREFIX
        keys: list[str] = []
        paginator = self.minio.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if key.endswith(".html.gz"):
                    keys.append(key)

        counters = {"success": 0, "skipped": 0, "failed": 0}
        workers = max(1, min(max_workers, len(keys)))

        def _parse(key: str):
            return self.process_one(key, force=force)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_parse, k): k for k in keys}
            for fut in as_completed(futures):
                key = futures[fut]
                try:
                    parsed_key = fut.result()
                    counters["skipped" if parsed_key is None else "success"] += 1
                except Exception as e:
                    logger.error(f"Parse failed {key}: {e}")
                    counters["failed"] += 1

        logger.info(f"batch done: {counters} ({len(keys)} files, {workers} workers)")
        return counters
