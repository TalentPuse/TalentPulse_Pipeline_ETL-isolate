"""Load parsed VietnamWorks job-detail JSON from MinIO into raw.job_detail.

v2: validates each payload before upsert. Rejected rows are quarantined in
raw.job_detail_rejects with a reason code + detail — full audit trail, replay
possible by re-running the loader after relaxing a rule.
"""
import argparse
import json
import logging
from datetime import datetime, timezone

from src.loaders.validators import validate
from src.storage.job_detail_repo import JobDetailRepo
from src.storage.minio_client import MinioClient
from src.utils.config import config

logger = logging.getLogger(__name__)


PARSED_PREFIX = "parsed/details/vietnamworks/"


class JobDetailLoader:
    def __init__(
        self,
        minio: MinioClient | None = None,
        repo: JobDetailRepo | None = None,
        *,
        validate_payload: bool = True,
    ):
        self.minio = minio or MinioClient()
        self.repo = repo or JobDetailRepo()
        self.bucket = config.S3_BUCKET_NAME
        self.validate_payload = validate_payload

    def _read_json(self, key: str) -> dict | None:
        try:
            body = self.minio.s3_client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            return json.loads(body)
        except json.JSONDecodeError as e:
            logger.error(f"malformed JSON {key}: {e}")
            return None

    def load_one(self, key: str, *, dry_run: bool = False) -> str:
        """Returns one of: 'loaded', 'rejected', 'failed'."""
        payload = self._read_json(key)
        if payload is None:
            return "failed"
        if not payload.get("source") or not payload.get("source_job_id"):
            logger.error(f"missing required fields in {key}")
            return "failed"

        if self.validate_payload:
            reject = validate(payload)
            if reject is not None:
                reason, detail = reject
                logger.info(
                    f"REJECT {payload.get('source_job_id')} reason={reason} detail={detail}"
                )
                if not dry_run:
                    try:
                        self.repo.record_reject(payload, reason, detail, minio_key=key)
                    except Exception as e:
                        logger.exception(f"record_reject failed for {key}: {e}")
                        return "failed"
                return "rejected"

        if dry_run:
            return "loaded"
        try:
            self.repo.upsert(payload)
            return "loaded"
        except Exception as e:
            logger.exception(f"upsert failed for {key}: {e}")
            return "failed"

    def run_batch(self, prefix: str = PARSED_PREFIX, *, dry_run: bool = False,
                  since: datetime | None = None) -> dict:
        counters = {"loaded": 0, "rejected": 0, "skipped": 0, "failed": 0}
        paginator = self.minio.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith(".json"):
                    continue
                if since is not None:
                    last_mod = obj.get("LastModified")
                    if last_mod and last_mod < since:
                        counters["skipped"] += 1
                        continue
                outcome = self.load_one(key, dry_run=dry_run)
                counters[outcome] += 1
        logger.info(f"load done: {counters}  (dry_run={dry_run})")
        return counters


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    # Accept "YYYY-MM-DD" or full ISO
    try:
        if "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(value, "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as e:
        raise SystemExit(f"invalid --since value '{value}': {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO date/datetime, only load files modified after this")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    JobDetailLoader().run_batch(dry_run=args.dry_run, since=_parse_since(args.since))


if __name__ == "__main__":
    main()
