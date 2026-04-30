"""LinkedIn detail page parser: HTML -> JobDetail via regex extraction."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from src.parsers.base import MinIOParser
from src.parsers.vietnamworks.detail.html_cleaner import strip_html
from src.parsers.vietnamworks.detail.schema import JobDetail
from src.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)


class LinkedInParseError(Exception):
    pass


def _extract_text(pattern: str, html: str) -> str | None:
    m = re.search(pattern, html, re.DOTALL)
    if not m:
        return None
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


def _parse_criteria(html: str) -> list[str]:
    """Extract the 4 job criteria: level, type, function, industries."""
    raw = re.findall(r'description__job-criteria-text[^>]*>(.*?)<', html, re.DOTALL)
    return [c.strip() for c in raw]


def _parse_applicants(html: str) -> int | None:
    m = re.search(r'num-applicants__caption[^>]*>(.*?)<', html, re.DOTALL)
    if not m:
        return None
    text = m.group(1).strip()
    nums = re.findall(r'\d+', text.replace(",", ""))
    return int(nums[0]) if nums else None


def _parse_relative_date(text: str | None) -> str | None:
    """Convert '2 days ago', '1 week ago' etc. to ISO date string."""
    if not text:
        return None
    text = text.strip().lower()
    now = datetime.now(timezone.utc)

    m = re.search(r'(\d+)\s*(second|minute|hour|day|week|month)', text)
    if not m:
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")

    n = int(m.group(1))
    unit = m.group(2)
    deltas = {
        "second": timedelta(seconds=n),
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=n * 30),
    }
    dt = now - deltas.get(unit, timedelta())
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_location(html: str) -> list[dict]:
    m = re.search(r'topcard__flavor--bullet[^>]*>(.*?)<', html, re.DOTALL)
    if not m:
        return []
    city = m.group(1).strip()
    if not city:
        return []
    return [{"city": city}]


def _parse_salary(html: str) -> str | None:
    m = re.search(r'salary-main-rail__data-body[^>]*>(.*?)<', html, re.DOTALL)
    if m:
        return m.group(1).strip() or None
    m = re.search(r'compensation__salary[^>]*>(.*?)<', html, re.DOTALL)
    if m:
        return m.group(1).strip() or None
    return None


def _parse_company_logo(html: str) -> str | None:
    m = re.search(r'artdeco-entity-image[^>]*(?:data-ghost-url|src)="([^"]+)"', html)
    return m.group(1) if m else None


class LinkedInDetailParser(MinIOParser):
    VERSION = "linkedin-v1"
    HTML_PREFIX = "details/linkedin/html/"
    PARSED_PREFIX = "parsed/details/linkedin/"

    def __init__(self, minio: MinioClient | None = None):
        super().__init__(minio)

    def parse_html(self, html: str, source_job_id: str | None = None) -> JobDetail:
        title = _extract_text(r'top-card-layout__title[^>]*>(.*?)<', html)
        if not title:
            raise LinkedInParseError("No title found")

        company = _extract_text(r'topcard__org-name-link[^>]*>(.*?)<', html)
        criteria = _parse_criteria(html)
        posted_text = _extract_text(r'posted-time-ago__text[^>]*>(.*?)<', html)

        desc_match = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', html, re.DOTALL)
        description = strip_html(desc_match.group(1)) if desc_match else None

        return JobDetail(
            source="linkedin",
            source_job_id=source_job_id or "",
            source_url=f"https://www.linkedin.com/jobs/view/{source_job_id}" if source_job_id else None,
            parser_version=self.VERSION,
            parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=title,
            company_name=company,
            company_logo_url=_parse_company_logo(html),
            job_level=criteria[0] if len(criteria) > 0 else None,
            employment_type=criteria[1] if len(criteria) > 1 else None,
            job_function=criteria[2] if len(criteria) > 2 else None,
            industries=[{"name": criteria[3]}] if len(criteria) > 3 else [],
            locations=_parse_location(html),
            skills=[],
            benefits=[],
            job_description_text=description,
            pretty_salary=_parse_salary(html),
            num_of_applications=_parse_applicants(html),
            posted_at=_parse_relative_date(posted_text),
            is_active=True,
            is_expired=False,
        )
