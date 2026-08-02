"""Tests for the CareerViet parser.

Each test here maps to a quirk found in live data on 2026-08-02, not to a
hypothetical. Two of them (`monthsOfExperience`, `addressLocality`) cover bugs
the first version of the parser actually shipped with — it recorded 36 years of
experience and put the district in the city column.
"""
import pytest

from src.parsers.careerviet.detail_parser import (
    CareerVietDetailParser,
    CareerVietParseError,
    clean_employment_type,
    extract_job_posting,
    parse_locations,
    parse_salary,
    parse_work_hours,
    parse_years_of_experience,
)
from tests.parsers.careerviet._fixtures import (
    HTML_NO_JOBPOSTING,
    JOB_HTML,
    JOB_HTML_NUMERIC_SALARY,
)


@pytest.fixture
def parser():
    # parse_html is pure; no MinIO client needed.
    p = CareerVietDetailParser.__new__(CareerVietDetailParser)
    p.VERSION = CareerVietDetailParser.VERSION
    return p


class TestExtraction:
    def test_finds_jobposting_among_other_blocks(self):
        data = extract_job_posting(JOB_HTML)
        assert data is not None
        assert data["@type"] == "JobPosting"

    def test_returns_none_when_absent(self):
        assert extract_job_posting(HTML_NO_JOBPOSTING) is None

    def test_parser_raises_when_absent(self, parser):
        with pytest.raises(CareerVietParseError):
            parser.parse_html(HTML_NO_JOBPOSTING)


class TestSalary:
    def test_label_salary_is_not_invented(self):
        """"Cạnh tranh" means negotiable — it must not become a number."""
        smin, smax, currency, display, visible = parse_salary(
            {"currency": "VND", "value": {"value": "Cạnh tranh"}}
        )
        assert smin is None and smax is None
        assert visible is False
        assert currency == "VND"
        assert display == "Cạnh tranh"

    def test_numeric_band_is_kept(self):
        smin, smax, currency, _, visible = parse_salary(
            {"currency": "USD", "value": {"minValue": 2000, "maxValue": 3500}}
        )
        assert (smin, smax, currency, visible) == (2000.0, 3500.0, "USD", True)

    def test_missing_base_salary(self):
        assert parse_salary(None) == (None, None, None, None, False)


class TestEmploymentType:
    def test_double_encoded_list(self):
        """Live shape is ["\\"FULL_TIME\\""] — a list holding a JSON string."""
        assert clean_employment_type(['"FULL_TIME"']) == "FULL_TIME"

    def test_plain_string(self):
        assert clean_employment_type("FULL_TIME") == "FULL_TIME"

    def test_none_and_empty(self):
        assert clean_employment_type(None) is None
        assert clean_employment_type([]) is None


class TestExperience:
    def test_months_are_converted_to_years(self):
        """36 months is 3 years. Reading the raw integer gave 36 years."""
        assert parse_years_of_experience(
            {"monthsOfExperience": 36, "description": "Kinh nghiem 3 Nam"}
        ) == 3

    def test_sub_year_experience(self):
        assert parse_years_of_experience({"monthsOfExperience": 6}) == 0

    def test_falls_back_to_description(self):
        assert parse_years_of_experience({"description": "Kinh nghiem 5 Nam"}) == 5

    def test_none(self):
        assert parse_years_of_experience(None) is None


class TestLocations:
    def test_province_comes_from_address_locality(self):
        """CareerViet puts the DISTRICT in addressRegion and the PROVINCE in
        addressLocality — taking addressRegion yields "Quận 7", which matches
        nothing in city_map."""
        out = parse_locations(
            {
                "address": {
                    "streetAddress": "Quận 7, Hồ Chí Minh",
                    "addressRegion": "Quận 7",
                    "addressLocality": "Hồ Chí Minh",
                    "addressCountry": "VN",
                }
            }
        )
        assert out[0]["city"] == "Hồ Chí Minh"
        assert out[0]["district"] == "Quận 7"

    def test_handles_missing_address(self):
        assert parse_locations({"@type": "Place"}) == []
        assert parse_locations(None) == []


class TestWorkHours:
    def test_range(self):
        assert parse_work_hours("8:00-17:00") == ("8:00", "17:00")

    def test_garbage(self):
        assert parse_work_hours("linh hoat") == (None, None)
        assert parse_work_hours(None) == (None, None)


class TestEndToEnd:
    def test_full_parse(self, parser):
        d = parser.parse_html(JOB_HTML, source_job_id="IGNORED")
        # identifier.value wins over the id passed in from the object key
        assert d.source_job_id == "35C7EA19"
        assert d.source == "careerviet"
        assert d.parser_version == "careerviet-v1"
        assert d.title == "Data Analyst (Growth Analytics)"
        assert d.company_name == "FPT LONG CHAU"
        assert d.employment_type == "FULL_TIME"
        assert d.years_of_experience == 3
        assert d.locations[0]["city"] == "Hồ Chí Minh"
        assert d.expired_at == "2026-08-15T23:59:00Z"
        assert d.is_salary_visible is False
        assert d.working_from_hour == "8:00"

    def test_seo_skills_are_dropped(self, parser):
        """`skills` on CareerViet is keyword stuffing — a Data Analyst posting
        lists "Kiến trúc sư" and "Structural Drafter". The LLM extractor derives
        skills from the description instead."""
        d = parser.parse_html(JOB_HTML)
        assert d.skills == []

    def test_numeric_salary_posting(self, parser):
        d = parser.parse_html(JOB_HTML_NUMERIC_SALARY)
        assert d.source_job_id == "35C7F173"
        assert (d.salary_min, d.salary_max) == (2000.0, 3500.0)
        assert d.is_salary_visible is True
        assert d.salary_currency == "USD"
