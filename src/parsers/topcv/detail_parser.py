"""TopCV detail page parser: HTML -> JobDetail via JSON-LD JobPosting extraction."""
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

# Salary value strings that mean "negotiable / not disclosed", not a real figure.
_SALARY_SENTINELS = {"thoả thuận", "thỏa thuận", "cạnh tranh", "negotiable"}


class TopCVParseError(Exception):
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
    """Find and parse a JSON-LD script block with the given @type (handles arrays)."""
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == target_type:
                return item
    return None


def _parse_locations(job_location) -> list[dict]:
    """Normalize TopCV jobLocation (single dict OR list) into our location format."""
    if isinstance(job_location, dict):
        job_location = [job_location]
    out: list[dict] = []
    for loc in (job_location or []):
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address", {})
        if not isinstance(addr, dict):
            continue
        region = addr.get("addressRegion")
        out.append({
            "city": region,
            "city_vi": region,
            "district": addr.get("addressLocality"),
            "address": addr.get("streetAddress"),
        })
    return out


def _parse_experience(exp_req) -> int | None:
    """Extract whole years of experience from experienceRequirements."""
    if not isinstance(exp_req, dict):
        return None
    months = exp_req.get("monthsOfExperience")
    if months is None:
        return None
    try:
        return max(1, int(float(months)) // 12)
    except (ValueError, TypeError):
        return None


def _parse_skills(skills) -> list[dict]:
    """Parse skills (comma string) into a list of dicts; TopCV often omits this."""
    if not skills or not isinstance(skills, str):
        return []
    return [{"name": s.strip()} for s in skills.split(",") if s.strip()]


class TopCVDetailParser(MinIOParser):
    VERSION = "topcv-v1"
    HTML_PREFIX = "details/topcv/html/"
    PARSED_PREFIX = "parsed/details/topcv/"

    def __init__(self, minio: MinioClient | None = None):
        super().__init__(minio)

    def parse_html(self, html: str, source_job_id: str | None = None) -> JobDetail:
        data = _extract_json_ld(html, "JobPosting")
        if data is None:
            raise TopCVParseError("No JobPosting JSON-LD found")

        org = data.get("hiringOrganization") or {}
        salary = data.get("baseSalary") or {}
        salary_val = salary.get("value") or {}

        salary_display = None
        salary_min = None
        salary_max = None
        is_salary_visible = False

        if isinstance(salary_val, dict):
            raw_val = salary_val.get("value")
            if isinstance(raw_val, str) and raw_val.strip().lower() not in _SALARY_SENTINELS:
                salary_display = raw_val.strip() or None

            mv = salary_val.get("minValue")
            xv = salary_val.get("maxValue")
            if mv is not None or xv is not None:
                try:
                    salary_min = float(mv) if mv is not None else None
                    salary_max = float(xv) if xv is not None else None
                    is_salary_visible = salary_min is not None or salary_max is not None
                except (ValueError, TypeError):
                    pass

        job_id = source_job_id or ""
        source_url = f"https://www.topcv.vn/viec-lam/{job_id}.html" if job_id else None

        return JobDetail(
            source="topcv",
            source_job_id=job_id,
            source_url=source_url,
            parser_version=self.VERSION,
            parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=data.get("title"),
            company_name=org.get("name") if isinstance(org, dict) else None,
            company_logo_url=org.get("logo") if isinstance(org, dict) else None,
            company_profile_text=None,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary.get("currency"),
            is_salary_visible=is_salary_visible,
            pretty_salary=salary_display,
            years_of_experience=_parse_experience(data.get("experienceRequirements")),
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
