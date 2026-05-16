"""ITviec detail page parser: HTML -> JobDetail via JSON-LD extraction."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from src.parsers.base import MinIOParser
from src.parsers.vietnamworks.detail.html_cleaner import strip_html
from src.parsers.vietnamworks.detail.schema import JobDetail
from src.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)


class ITviecParseError(Exception):
    pass


def _is_past(dt_str: str | None) -> bool:
    """Return True if the ISO-8601 date/datetime is in the past."""
    if not dt_str:
        return False
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


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


class ITviecDetailParser(MinIOParser):
    VERSION = "itviec-v1"
    HTML_PREFIX = "details/itviec/html/"
    PARSED_PREFIX = "parsed/details/itviec/"

    def __init__(self, minio: MinioClient | None = None):
        super().__init__(minio)

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
        salary_min = None
        salary_max = None
        is_salary_visible = False

        if isinstance(salary_val, dict):
            raw_val = salary_val.get("value")
            if raw_val and isinstance(raw_val, str) and raw_val not in ("You'll love it",):
                salary_display = raw_val

            # Extract structured min/max from JSON-LD QuantitativeValue
            mv = salary_val.get("minValue")
            xv = salary_val.get("maxValue")
            if mv is not None or xv is not None:
                try:
                    salary_min = float(mv) if mv is not None else None
                    salary_max = float(xv) if xv is not None else None
                    is_salary_visible = salary_min is not None or salary_max is not None
                except (ValueError, TypeError):
                    pass

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
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary.get("currency"),
            is_salary_visible=is_salary_visible,
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
            is_expired=_is_past(data.get("validThrough")),
            is_active=not _is_past(data.get("validThrough")),
        )
