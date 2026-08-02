"""Parse CareerViet detail pages via their schema.org JobPosting JSON-LD.

CareerViet embeds a complete `JobPosting` block on every detail page (verified
on 5/5 sampled pages), so there is no HTML scraping here at all — no selectors to
drift, no BeautifulSoup. That makes this the most robust parser in the repo, and
it is the reason CareerViet was chosen over CareerLink and JobsGo.

`validThrough` is a real ISO timestamp, which matters: LinkedIn publishes no
expiry at all and needs the `job_stale_days` fallback in bronze. CareerViet rows
carry a genuine `expired_at`.

Four quirks in their JSON-LD, all measured rather than assumed:

1. `baseSalary.value.value` is frequently a STRING like "Cạnh tranh"
   (negotiable), not a number. Parsing it as numeric would either crash or
   fabricate a salary.
2. `employmentType` arrives double-encoded: `["\"FULL_TIME\""]` — a list holding
   a JSON string, quotes and all.
3. `skills` is SEO keyword stuffing, not skills. A Data Engineer posting listed
   "Kiến trúc sư, Nhân viên thiết kế kiến trúc, Structural Drafter". It is
   deliberately NOT mapped; the LLM extractor derives skills from the
   description instead.
4. `identifier` is a PropertyValue whose `name` is the COMPANY and whose `value`
   is the job id — easy to read backwards.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from src.parsers.base import MinIOParser
from src.parsers.vietnamworks.detail.schema import JobDetail
from src.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)

JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# "Cạnh tranh", "Thương lượng", "Negotiable" — a label, not a number.
_NUMERIC_RE = re.compile(r"\d[\d.,]*")


class CareerVietParseError(Exception):
    """Raised when a page carries no usable JobPosting block."""


def extract_job_posting(html: str) -> dict | None:
    """Return the first schema.org JobPosting object in the page, if any."""
    for block in JSON_LD_RE.findall(html):
        block = block.strip()
        if not block:
            continue
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def clean_employment_type(raw) -> str | None:
    """Unwrap CareerViet's double-encoded employmentType.

    Observed shape: ["\"FULL_TIME\""] — a list holding a JSON-quoted string.
    Also tolerates the sane shapes in case they fix it later.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    # Strip one layer of JSON quoting, e.g. '"FULL_TIME"' -> 'FULL_TIME'
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            value = value[1:-1]
    return value.strip() or None


def parse_salary(base_salary: dict | None) -> tuple[float | None, float | None, str | None, str | None, bool]:
    """Return (min, max, currency, display, is_visible) from a MonetaryAmount.

    Returns NULLs rather than guessing when the value is a label such as
    "Cạnh tranh". A wrong salary is worse than a missing one: these columns feed
    the salary marts, where one bogus row moves the percentile for everybody.
    """
    if not isinstance(base_salary, dict):
        return None, None, None, None, False

    currency = base_salary.get("currency") or None
    value = base_salary.get("value")
    if not isinstance(value, dict):
        return None, None, currency, None, False

    raw = value.get("value")
    display = raw if isinstance(raw, str) and raw.strip() else None

    smin = _coerce_number(value.get("minValue"))
    smax = _coerce_number(value.get("maxValue"))

    # Some rows put a single figure in `value` instead of a min/max pair.
    if smin is None and smax is None and isinstance(raw, (int, float)):
        smin = smax = float(raw)

    visible = smin is not None or smax is not None
    return smin, smax, currency, display, visible


def _coerce_number(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        m = _NUMERIC_RE.search(raw.replace(",", ""))
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def parse_locations(job_location) -> list[dict]:
    """Flatten jobLocation into the `[{"city": ...}]` shape silver expects.

    CareerViet inverts the usual schema.org meaning of these two fields:

        streetAddress   "Quận 7, Hồ Chí Minh"   district + province
        addressRegion   "Quận 7"                the DISTRICT
        addressLocality "Hồ Chí Minh"           the PROVINCE

    so `addressLocality` is the one to use. Taking `addressRegion` yields
    "Quận 7", which matches nothing in city_map and leaves `city_canonical`
    NULL — the same failure LinkedIn locations already caused.
    """
    entries = job_location if isinstance(job_location, list) else [job_location]
    out: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        addr = entry.get("address")
        if not isinstance(addr, dict):
            continue
        city = addr.get("addressLocality") or addr.get("addressRegion") or addr.get("streetAddress")
        if not city:
            continue
        out.append(
            {
                "city": str(city).strip(),
                "district": (addr.get("addressRegion") or "").strip() or None,
                "address": (addr.get("streetAddress") or "").strip() or None,
                "country": addr.get("addressCountry") or "VN",
            }
        )
    return out


def parse_years_of_experience(raw) -> int | None:
    """Read `monthsOfExperience` and convert to whole years.

    The field is an OccupationalExperienceRequirements object carrying MONTHS:
    `{"monthsOfExperience": 36, "description": "Kinh nghiệm 3 Năm"}`. Grabbing
    the first integer out of it — as a naive text parse does — records 36 years
    of experience for a 3-year role.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        months = raw.get("monthsOfExperience")
        if isinstance(months, (int, float)) and months > 0:
            return max(1, int(months) // 12) if months >= 12 else 0
        raw = raw.get("description") or ""
    if isinstance(raw, str):
        m = re.search(r"(\d+)", raw)
        if m:
            return int(m.group(1))
    return None


def parse_work_hours(raw) -> tuple[str | None, str | None]:
    """`workHours` is a range string like "8:00-17:00"."""
    if not isinstance(raw, str) or "-" not in raw:
        return None, None
    start, _, end = raw.partition("-")
    return start.strip() or None, end.strip() or None


class CareerVietDetailParser(MinIOParser):
    VERSION = "careerviet-v1"
    HTML_PREFIX = "details/careerviet/html/"
    PARSED_PREFIX = "parsed/details/careerviet/"

    def __init__(self, minio: MinioClient | None = None):
        super().__init__(minio)

    def parse_html(self, html: str, source_job_id: str | None = None) -> JobDetail:
        data = extract_job_posting(html)
        if data is None:
            raise CareerVietParseError("No JobPosting JSON-LD found")

        org = data.get("hiringOrganization")
        org = org if isinstance(org, dict) else {}

        identifier = data.get("identifier")
        # identifier.name is the COMPANY, identifier.value is the job id.
        ident_value = identifier.get("value") if isinstance(identifier, dict) else None

        smin, smax, currency, salary_display, salary_visible = parse_salary(data.get("baseSalary"))
        work_from, work_to = parse_work_hours(data.get("workHours"))

        industries = []
        industry = data.get("industry")
        if isinstance(industry, str) and industry.strip():
            industries = [{"name": part.strip()} for part in industry.split(",") if part.strip()]

        benefits = []
        raw_benefits = data.get("jobBenefits")
        if isinstance(raw_benefits, str) and raw_benefits.strip():
            benefits = [{"name": raw_benefits.strip()}]
        elif isinstance(raw_benefits, list):
            benefits = [{"name": str(b).strip()} for b in raw_benefits if str(b).strip()]

        return JobDetail(
            source="careerviet",
            source_job_id=str(ident_value or source_job_id or "").upper(),
            source_url=data.get("url"),
            parser_version=self.VERSION,
            parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=data.get("title"),
            company_name=org.get("name"),
            company_logo_url=org.get("logo"),
            salary_min=smin,
            salary_max=smax,
            salary_currency=currency,
            is_salary_visible=salary_visible,
            pretty_salary=salary_display,
            years_of_experience=parse_years_of_experience(data.get("experienceRequirements")),
            employment_type=clean_employment_type(data.get("employmentType")),
            locations=parse_locations(data.get("jobLocation")),
            industries=industries,
            # `skills` is intentionally left empty — see the module docstring.
            skills=[],
            benefits=benefits,
            job_description_text=data.get("description"),
            posted_at=data.get("datePosted"),
            expired_at=data.get("validThrough"),
            working_from_hour=work_from,
            working_to_hour=work_to,
        )
