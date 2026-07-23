import gzip
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.parsers.topcv.detail_parser import TopCVDetailParser, TopCVParseError

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "topcv" / "detail_2114998.html.gz"

# MinioClient() refuses to build without S3 credentials, so these parse-only
# tests inject a stub instead of letting the parser construct a real client —
# CI has no creds, and a local .env silently hid that.


@pytest.fixture
def html() -> str:
    return gzip.decompress(FIXTURE.read_bytes()).decode("utf-8")


def test_parse_real_fixture(html):
    detail = TopCVDetailParser(minio=MagicMock()).parse_html(html, source_job_id="2114998")
    assert detail.source == "topcv"
    # job id comes from the arg, NOT identifier.value (which is the company id 246114)
    assert detail.source_job_id == "2114998"
    assert detail.source_url == "https://www.topcv.vn/viec-lam/data-engineer-junior-middle/2114998.html"
    assert detail.title == "Data Engineer (Junior/Middle)"
    assert "VIETTEL" in (detail.company_name or "")
    assert detail.parser_version == "topcv-v1"
    # negotiable salary "Thoả thuận" -> not visible, no min/max
    assert detail.is_salary_visible is False
    assert detail.salary_min is None and detail.salary_max is None
    assert detail.salary_currency == "VND"
    # experienceRequirements.monthsOfExperience == 12 -> 1 year
    assert detail.years_of_experience == 1
    assert detail.employment_type == "FULL_TIME"
    # single-dict jobLocation normalized to one location, city from addressRegion
    assert len(detail.locations) == 1
    assert detail.locations[0]["city"] == "Hà Nội"
    assert detail.job_description_text and "Mô tả công việc" in detail.job_description_text
    # TODO: fixture validThrough 2026-08-04 — revisit after that date
    assert detail.is_expired is False


def test_parse_missing_jobposting_raises():
    with pytest.raises(TopCVParseError):
        TopCVDetailParser(minio=MagicMock()).parse_html("<html>no json-ld</html>")


def test_source_url_falls_back_when_no_canonical_link():
    html = """
    <html><head>
    <script type="application/ld+json">
    {
        "@type": "JobPosting",
        "title": "Fallback Job",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "baseSalary": {"@type": "MonetaryAmount", "currency": "VND", "value": {"@type": "QuantitativeValue", "value": "Thoả thuận"}},
        "datePosted": "2026-01-01",
        "validThrough": "2099-01-01"
    }
    </script>
    </head><body></body></html>
    """
    detail = TopCVDetailParser(minio=MagicMock()).parse_html(html, source_job_id="999")
    assert detail.source_url == "https://www.topcv.vn/viec-lam/999.html"
