"""ITviec detail page parser: HTML → JobDetail via JSON-LD extraction."""
from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from src.parsers.vietnamworks.detail.html_cleaner import strip_html
from src.parsers.vietnamworks.detail.schema import JobDetail
from src.storage.minio_client import MinioClient
from src.utils.config import config

logger = logging.getLogger(__name__)

PARSED_PREFIX = "parsed/details/itviec/"
HTML_PREFIX = "details/itviec/html/"


class ITviecParseError(Exception):
    pass


def _extract_json_ld(html: str, target_type: str = "JobPosting") -> dict | None:
    """Find and parse a JSON-LD script block with the given @type."""
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("@type") == target_type:
            return data
    return None


def _parse_skills(skills_str: str | None) -> list[dict]:
    """Parse comma-separated skills string into list of dicts."""
    if not skills_str:
        return []
    return [{"name": s.strip()} for s in skills_str.split(",") if s.strip()]


def _parse_locations(job_locations: list | None) -> list[dict]:
    """Parse JobPosting jobLocation array into our location format."""
    out = []
    for loc in (job_locations or []):
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address", {})
        if not isinstance(addr, dict):
            continue
        out.append({
            "city": addr.get("addressRegion"),
            "city_vi": addr.get("addressRegion"),
            "address": addr.get("streetAddress"),
        })
    return out


def _parse_experience(exp_req: dict | None) -> int | None:
    """Extract years of experience from experienceRequirements."""
    if not isinstance(exp_req, dict):
        return None
    months = exp_req.get("monthsOfExperience")
    if months is None:
        return None
    try:
        return max(1, int(float(months)) // 12)
    except (ValueError, TypeError):
        return None


def _extract_job_id_from_key(html_key: str) -> str:
    """Extract job_id from MinIO object key like 'details/itviec/html/run/4611.html.gz'."""
    return html_key.split("/")[-1].replace(".html.gz", "")


class ITviecDetailParser:
    VERSION = "itviec-v1"

    def __init__(self, minio: MinioClient | None = None):
        self.minio = minio or MinioClient()
        self.bucket = config.S3_BUCKET_NAME

    def parse_html(self, html: str, source_job_id: str | None = None) -> JobDetail:
        """Parse ITviec detail HTML into a JobDetail via JSON-LD."""
        data = _extract_json_ld(html, "JobPosting")
        if data is None:
            raise ITviecParseError("No JobPosting JSON-LD found")

        org = data.get("hiringOrganization") or {}
        salary = data.get("baseSalary") or {}
        salary_val = salary.get("value") or {}
        exp_req = data.get("experienceRequirements")

        salary_display = None
        if isinstance(salary_val, dict):
            raw_val = salary_val.get("value")
            if raw_val and isinstance(raw_val, str) and raw_val not in ("You'll love it",):
                salary_display = raw_val

        return JobDetail(
            source="itviec",
            source_job_id=source_job_id or "",
            source_url=data.get("potentialAction", {}).get("target", "").replace(
                "/job_applications/new", ""
            ) if isinstance(data.get("potentialAction"), dict) else None,
            parser_version=self.VERSION,
            parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=data.get("title"),
            company_name=org.get("name") if isinstance(org, dict) else None,
            company_logo_url=org.get("logo") if isinstance(org, dict) else None,
            company_profile_text=org.get("description") if isinstance(org, dict) else None,
            salary_currency=salary.get("currency"),
            is_salary_visible=False,
            pretty_salary=salary_display,
            years_of_experience=_parse_experience(exp_req),
            employment_type=data.get("employmentType"),
            job_function=data.get("industry") or "IT",
            locations=_parse_locations(data.get("jobLocation")),
            skills=_parse_skills(data.get("skills")),
            benefits=[],
            job_description_text=strip_html(data.get("description")),
            job_requirement_text=None,
            posted_at=data.get("datePosted"),
            expired_at=data.get("validThrough"),
            is_expired=False,
            is_active=True,
        )

    def _parsed_key(self, job_id: str) -> str:
        return f"{PARSED_PREFIX}{job_id}.json"

    def _exists(self, key: str) -> bool:
        try:
            self.minio.s3_client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def process_one(self, html_object_key: str, *, force: bool = False) -> str | None:
        """Parse one HTML.gz from MinIO, write JSON result. Returns parsed key or None."""
        job_id = _extract_job_id_from_key(html_object_key)
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

    def run_batch(self, prefix: str = HTML_PREFIX, *, force: bool = False) -> dict:
        """Parse all ITviec HTML.gz files in MinIO."""
        counters = {"success": 0, "skipped": 0, "failed": 0}
        paginator = self.minio.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith(".html.gz"):
                    continue
                try:
                    parsed_key = self.process_one(key, force=force)
                    if parsed_key is None:
                        counters["skipped"] += 1
                    else:
                        counters["success"] += 1
                except ITviecParseError as e:
                    logger.error(f"Parse failed {key}: {e}")
                    counters["failed"] += 1
                except Exception as e:
                    logger.exception(f"Unexpected error on {key}: {e}")
                    counters["failed"] += 1
        logger.info(f"ITviec parse batch done: {counters}")
        return counters
