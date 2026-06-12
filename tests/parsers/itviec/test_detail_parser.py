"""Tests for ITviec detail parser — JSON-LD extraction + field mapping."""
import copy
import gzip
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.parsers.itviec.detail_parser import (
    ITviecDetailParser,
    ITviecParseError,
    _extract_json_ld,
    _parse_skills,
    _parse_locations,
    _parse_experience,
    _extract_job_id_from_key,
)
from tests.parsers.itviec._fixtures import (
    SAMPLE_JOB_POSTING,
    make_detail_html,
)


# ── _extract_json_ld ────────────────────────────────────────────────


class TestExtractJsonLd:
    def test_finds_job_posting(self):
        html = make_detail_html()
        data = _extract_json_ld(html, "JobPosting")
        assert data is not None
        assert data["@type"] == "JobPosting"
        assert data["title"] == "Senior Data Engineer"

    def test_finds_breadcrumb_list(self):
        html = make_detail_html()
        data = _extract_json_ld(html, "BreadcrumbList")
        assert data is not None
        assert data["@type"] == "BreadcrumbList"

    def test_returns_none_for_missing_type(self):
        html = make_detail_html()
        assert _extract_json_ld(html, "Organization") is None

    def test_returns_none_for_no_json_ld(self):
        html = "<html><body>plain page</body></html>"
        assert _extract_json_ld(html, "JobPosting") is None

    def test_skips_malformed_json(self):
        html = '''
        <script type="application/ld+json">{broken json</script>
        <script type="application/ld+json">{"@type":"JobPosting","title":"OK"}</script>
        '''
        data = _extract_json_ld(html, "JobPosting")
        assert data is not None
        assert data["title"] == "OK"


# ── _parse_skills ────────────────────────────────────────────────────


class TestParseSkills:
    def test_comma_separated(self):
        result = _parse_skills("Python, SQL, Spark")
        assert result == [{"name": "Python"}, {"name": "SQL"}, {"name": "Spark"}]

    def test_none_input(self):
        assert _parse_skills(None) == []

    def test_empty_string(self):
        assert _parse_skills("") == []

    def test_single_skill(self):
        assert _parse_skills("Python") == [{"name": "Python"}]

    def test_strips_whitespace(self):
        result = _parse_skills("  Python ,  SQL  , Spark  ")
        assert result == [{"name": "Python"}, {"name": "SQL"}, {"name": "Spark"}]

    def test_trailing_comma(self):
        result = _parse_skills("Python, SQL,")
        assert result == [{"name": "Python"}, {"name": "SQL"}]


# ── _parse_locations ─────────────────────────────────────────────────


class TestParseLocations:
    def test_standard_location(self):
        locs = _parse_locations(SAMPLE_JOB_POSTING["jobLocation"])
        assert len(locs) == 1
        assert locs[0]["city"] == "Hồ Chí Minh"
        assert locs[0]["address"] == "123 Main St"

    def test_none_input(self):
        assert _parse_locations(None) == []

    def test_empty_list(self):
        assert _parse_locations([]) == []

    def test_non_dict_elements_skipped(self):
        assert _parse_locations(["not a dict", 123]) == []

    def test_missing_address_key(self):
        result = _parse_locations([{"@type": "Place"}])
        assert len(result) == 1
        assert result[0]["city"] is None

    def test_multiple_locations(self):
        multi = [
            {
                "@type": "Place",
                "address": {"addressRegion": "Hà Nội", "streetAddress": "456 St"},
            },
            {
                "@type": "Place",
                "address": {"addressRegion": "Đà Nẵng", "streetAddress": "789 St"},
            },
        ]
        result = _parse_locations(multi)
        assert len(result) == 2
        assert result[0]["city"] == "Hà Nội"
        assert result[1]["city"] == "Đà Nẵng"


# ── _parse_experience ────────────────────────────────────────────────


class TestParseExperience:
    def test_months_to_years(self):
        assert _parse_experience({"monthsOfExperience": 37}) == 3

    def test_exact_division(self):
        assert _parse_experience({"monthsOfExperience": 24}) == 2

    def test_less_than_12_months_becomes_1(self):
        assert _parse_experience({"monthsOfExperience": 6}) == 1

    def test_none_input(self):
        assert _parse_experience(None) is None

    def test_non_dict_input(self):
        assert _parse_experience("3 years") is None

    def test_missing_months_field(self):
        assert _parse_experience({"@type": "OccupationalExperienceRequirements"}) is None

    def test_non_numeric_months(self):
        assert _parse_experience({"monthsOfExperience": "many"}) is None

    def test_zero_months_becomes_one(self):
        assert _parse_experience({"monthsOfExperience": 0}) == 1

    def test_float_months(self):
        assert _parse_experience({"monthsOfExperience": 36.5}) == 3


# ── _extract_job_id_from_key ────────────────────────────────────────


class TestExtractJobIdFromKey:
    def test_standard_key(self):
        assert _extract_job_id_from_key("details/itviec/html/run123/4611.html.gz") == "4611"

    def test_nested_path(self):
        assert _extract_job_id_from_key("a/b/c/99999.html.gz") == "99999"


# ── ITviecDetailParser.parse_html ────────────────────────────────────


class TestParseHtml:
    def test_full_parse(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        html = make_detail_html()

        detail = parser.parse_html(html, source_job_id="4611")

        assert detail.source == "itviec"
        assert detail.source_job_id == "4611"
        assert detail.title == "Senior Data Engineer"
        assert detail.company_name == "Acme Corp"
        assert detail.company_logo_url == "https://itviec.com/rails/active_storage/logo.png"
        assert detail.company_profile_text == "A great company"
        assert detail.salary_currency == "USD"
        assert detail.pretty_salary is None  # "You'll love it" filtered out
        assert detail.is_salary_visible is False
        assert detail.years_of_experience == 3  # 37 months → 3
        assert detail.employment_type == "FULL_TIME"
        assert detail.job_function == "Information Technology"
        assert detail.posted_at == "2026-04-24"
        assert detail.expired_at == "2099-12-31"
        assert detail.parser_version == "itviec-v1"
        assert detail.is_active is True
        assert detail.is_expired is False

    def test_skills_parsed(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        html = make_detail_html()
        detail = parser.parse_html(html)
        skill_names = [s["name"] for s in detail.skills]
        assert "Data Engineer" in skill_names
        assert "Python" in skill_names
        assert "SQL" in skill_names

    def test_locations_parsed(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        html = make_detail_html()
        detail = parser.parse_html(html)
        assert len(detail.locations) == 1
        assert detail.locations[0]["city"] == "Hồ Chí Minh"

    def test_source_url_strips_apply_path(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        html = make_detail_html()
        detail = parser.parse_html(html)
        assert detail.source_url is not None
        assert "/job_applications/new" not in detail.source_url
        assert "senior-data-engineer-acme-corp-4611" in detail.source_url

    def test_description_html_stripped(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        html = make_detail_html()
        detail = parser.parse_html(html)
        assert "<ul>" not in (detail.job_description_text or "")
        assert "<li>" not in (detail.job_description_text or "")
        assert "Build data pipelines" in (detail.job_description_text or "")

    def test_raises_on_missing_json_ld(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        with pytest.raises(ITviecParseError, match="No JobPosting"):
            parser.parse_html("<html><body>no json-ld</body></html>")

    def test_salary_visible_when_real_value(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        posting = copy.deepcopy(SAMPLE_JOB_POSTING)
        posting["baseSalary"]["value"]["value"] = "$2000 - $3000"
        html = make_detail_html(posting)
        detail = parser.parse_html(html)
        assert detail.pretty_salary == "$2000 - $3000"

    def test_no_hiring_org(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        posting = copy.deepcopy(SAMPLE_JOB_POSTING)
        posting.pop("hiringOrganization")
        html = make_detail_html(posting)
        detail = parser.parse_html(html)
        assert detail.company_name is None

    def test_no_experience_requirements(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        posting = copy.deepcopy(SAMPLE_JOB_POSTING)
        posting.pop("experienceRequirements")
        html = make_detail_html(posting)
        detail = parser.parse_html(html)
        assert detail.years_of_experience is None

    def test_no_salary(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        posting = copy.deepcopy(SAMPLE_JOB_POSTING)
        posting.pop("baseSalary")
        html = make_detail_html(posting)
        detail = parser.parse_html(html)
        assert detail.salary_currency is None
        assert detail.pretty_salary is None

    def test_no_job_locations(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        posting = copy.deepcopy(SAMPLE_JOB_POSTING)
        posting.pop("jobLocation")
        html = make_detail_html(posting)
        detail = parser.parse_html(html)
        assert detail.locations == []

    def test_fallback_job_function(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        posting = copy.deepcopy(SAMPLE_JOB_POSTING)
        posting.pop("industry")
        html = make_detail_html(posting)
        detail = parser.parse_html(html)
        assert detail.job_function == "IT"

    def test_potential_action_not_dict(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        posting = copy.deepcopy(SAMPLE_JOB_POSTING)
        posting["potentialAction"] = "apply now"
        html = make_detail_html(posting)
        detail = parser.parse_html(html)
        assert detail.source_url is None

    def test_to_dict_round_trip(self):
        parser = ITviecDetailParser.__new__(ITviecDetailParser)
        html = make_detail_html()
        detail = parser.parse_html(html, source_job_id="4611")
        d = detail.to_dict()
        assert d["source"] == "itviec"
        assert d["source_job_id"] == "4611"
        assert isinstance(d["skills"], list)
        assert isinstance(d["locations"], list)
        json.dumps(d)  # must be JSON-serializable


# ── ITviecDetailParser.process_one ───────────────────────────────────


class TestProcessOne:
    @patch("src.parsers.base.config")
    def test_process_one_writes_parsed_json(self, mock_config):
        mock_config.S3_BUCKET_NAME = "test-bucket"
        minio = MagicMock()

        html = make_detail_html()
        compressed = gzip.compress(html.encode("utf-8"))
        minio.s3_client.get_object.return_value = {"Body": MagicMock(read=lambda: compressed)}
        minio.s3_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )

        parser = ITviecDetailParser(minio=minio)
        result = parser.process_one("details/itviec/html/run1/4611.html.gz")

        assert result == "parsed/details/itviec/4611.json"
        minio.upload_string.assert_called_once()
        call_args = minio.upload_string.call_args
        assert call_args[1]["content_type"] == "application/json" or call_args[0][3] == "application/json"

    @patch("src.parsers.base.config")
    def test_process_one_skips_existing(self, mock_config):
        mock_config.S3_BUCKET_NAME = "test-bucket"
        minio = MagicMock()
        minio.s3_client.head_object.return_value = {}  # exists

        parser = ITviecDetailParser(minio=minio)
        result = parser.process_one("details/itviec/html/run1/4611.html.gz")

        assert result is None
        minio.upload_string.assert_not_called()

    @patch("src.parsers.base.config")
    def test_process_one_force_overwrites(self, mock_config):
        mock_config.S3_BUCKET_NAME = "test-bucket"
        minio = MagicMock()
        minio.s3_client.head_object.return_value = {}  # exists

        html = make_detail_html()
        compressed = gzip.compress(html.encode("utf-8"))
        minio.s3_client.get_object.return_value = {"Body": MagicMock(read=lambda: compressed)}

        parser = ITviecDetailParser(minio=minio)
        result = parser.process_one("details/itviec/html/run1/4611.html.gz", force=True)

        assert result == "parsed/details/itviec/4611.json"
        minio.upload_string.assert_called_once()
