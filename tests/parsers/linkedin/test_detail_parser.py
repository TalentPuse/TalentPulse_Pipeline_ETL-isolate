"""Tests for LinkedIn detail parser — regex extraction from HTML."""
import pytest

from src.parsers.linkedin.detail_parser import (
    LinkedInDetailParser,
    LinkedInParseError,
    _extract_text,
    _parse_criteria,
    _parse_applicants,
    _parse_relative_date,
    _parse_location,
    _parse_salary,
    _parse_salary_structured,
    _parse_company_logo,
)


# ── Fixtures ─────────────────────────────────────────────────────


def make_html(
    title="Senior Data Engineer",
    company="Acme Corp",
    location="Ho Chi Minh City, Vietnam",
    criteria=None,
    description="<p>Build data pipelines using Python and Spark.</p>",
    salary_display=None,
    posted_text="2 days ago",
    applicants_text=None,
    logo_url=None,
):
    if criteria is None:
        criteria = ["Senior", "Full-time", "Engineering", "IT Services"]
    criteria_html = "".join(
        f'<span class="description__job-criteria-text">{c}</span>'
        for c in criteria
    )
    salary_html = ""
    if salary_display:
        salary_html = f'<div class="salary-main-rail__data-body">{salary_display}</div>'
    applicants_html = ""
    if applicants_text:
        applicants_html = f'<span class="num-applicants__caption">{applicants_text}</span>'
    posted_html = ""
    if posted_text:
        posted_html = f'<span class="posted-time-ago__text">{posted_text}</span>'
    logo_html = ""
    if logo_url:
        logo_html = f'<img class="artdeco-entity-image" data-ghost-url="{logo_url}" />'

    return f"""
    <html><body>
        <h1 class="top-card-layout__title">{title}</h1>
        <a class="topcard__org-name-link">{company}</a>
        <span class="topcard__flavor--bullet">{location}</span>
        {posted_html}
        {criteria_html}
        {salary_html}
        {applicants_html}
        {logo_html}
        <div class="show-more-less-html__markup">{description}</div>
    </body></html>
    """


# ── _extract_text ────────────────────────────────────────────────


class TestExtractText:
    def test_extracts_match(self):
        html = '<span class="foo">hello world</span>'
        assert _extract_text(r'class="foo"[^>]*>(.*?)<', html) == "hello world"

    def test_captures_first_text_node(self):
        """Regex (.*?)< captures up to first <, so nested tags give empty."""
        html = '<span class="foo"><b>hello</b> world</span>'
        result = _extract_text(r'class="foo"[^>]*>(.*?)<', html)
        assert result == ""

    def test_no_match_returns_none(self):
        html = '<span class="bar">text</span>'
        assert _extract_text(r'class="foo"[^>]*>(.*?)<', html) is None


# ── _parse_criteria ──────────────────────────────────────────────


class TestParseCriteria:
    def test_extracts_four_criteria(self):
        html = make_html(criteria=["Senior", "Full-time", "Engineering", "IT"])
        result = _parse_criteria(html)
        assert result == ["Senior", "Full-time", "Engineering", "IT"]

    def test_no_criteria_returns_empty(self):
        html = "<html><body>no criteria</body></html>"
        assert _parse_criteria(html) == []

    def test_empty_criteria_list(self):
        html = make_html(criteria=[])
        assert _parse_criteria(html) == []


# ── _parse_applicants ────────────────────────────────────────────


class TestParseApplicants:
    def test_extracts_number(self):
        html = '<span class="num-applicants__caption">25 applicants</span>'
        assert _parse_applicants(html) == 25

    def test_extracts_with_commas(self):
        html = '<span class="num-applicants__caption">1,234 applicants</span>'
        assert _parse_applicants(html) == 1234

    def test_no_match_returns_none(self):
        html = "<html>no applicants</html>"
        assert _parse_applicants(html) is None

    def test_text_without_number(self):
        html = '<span class="num-applicants__caption">Be among the first</span>'
        assert _parse_applicants(html) is None


# ── _parse_relative_date ────────────────────────────────────────


class TestParseRelativeDate:
    def test_none_input(self):
        assert _parse_relative_date(None) is None

    def test_empty_string(self):
        assert _parse_relative_date("") is None

    def test_days_ago(self):
        result = _parse_relative_date("2 days ago")
        assert result is not None
        assert "T" in result

    def test_weeks_ago(self):
        result = _parse_relative_date("1 week ago")
        assert result is not None

    def test_hours_ago(self):
        result = _parse_relative_date("3 hours ago")
        assert result is not None

    def test_months_ago(self):
        result = _parse_relative_date("1 month ago")
        assert result is not None

    def test_no_number_returns_now(self):
        result = _parse_relative_date("just now")
        assert result is not None


# ── _parse_location ──────────────────────────────────────────────


class TestParseLocation:
    def test_extracts_city(self):
        html = '<span class="topcard__flavor--bullet">Ho Chi Minh City, Vietnam</span>'
        result = _parse_location(html)
        assert len(result) == 1
        assert result[0]["city"] == "Ho Chi Minh City, Vietnam"

    def test_empty_city_returns_empty(self):
        html = '<span class="topcard__flavor--bullet">  </span>'
        result = _parse_location(html)
        assert result == []

    def test_no_match_returns_empty(self):
        html = "<html>no location</html>"
        assert _parse_location(html) == []


# ── _parse_salary ────────────────────────────────────────────────


class TestParseSalary:
    def test_main_rail_salary(self):
        html = '<div class="salary-main-rail__data-body">1,000,000 - 3,000,000 VND/month</div>'
        assert _parse_salary(html) == "1,000,000 - 3,000,000 VND/month"

    def test_compensation_salary(self):
        html = '<div class="compensation__salary">$50 - $80/hr</div>'
        assert _parse_salary(html) == "$50 - $80/hr"

    def test_no_salary(self):
        html = "<html>no salary</html>"
        assert _parse_salary(html) is None

    def test_empty_main_rail_returns_none(self):
        """First regex matches empty text, which strips to '' and returns None."""
        html = '<div class="salary-main-rail__data-body">  </div>'
        assert _parse_salary(html) is None

    def test_compensation_fallback_when_no_main_rail(self):
        html = '<div class="compensation__salary">$100</div>'
        assert _parse_salary(html) == "$100"


# ── _parse_salary_structured ────────────────────────────────────


class TestParseSalaryStructured:
    def test_vnd_monthly(self):
        html = '<div class="salary-main-rail__data-body">1,000,000 - 3,000,000 VND/month</div>'
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert sal_min == 1_000_000
        assert sal_max == 3_000_000
        assert currency == "VND"
        assert period == "monthly"

    def test_usd_hourly(self):
        html = '<div class="salary-main-rail__data-body">$50 - $80/hr</div>'
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert sal_min == 50
        assert sal_max == 80
        assert currency == "USD"
        assert period == "hourly"

    def test_vnd_dong_symbol(self):
        html = '<div class="salary-main-rail__data-body">₫1.000.000 - ₫3.000.000/tháng</div>'
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert sal_min == 1_000_000
        assert sal_max == 3_000_000
        assert currency == "VND"

    def test_usd_yearly(self):
        html = '<div class="salary-main-rail__data-body">$50,000 - $80,000/year</div>'
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert sal_min == 50_000
        assert sal_max == 80_000
        assert currency == "USD"
        assert period == "yearly"

    def test_single_number(self):
        html = '<div class="salary-main-rail__data-body">1,000,000 VND/month</div>'
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert sal_min == 1_000_000
        assert sal_max == 1_000_000

    def test_no_salary_returns_none(self):
        html = "<html>no salary info</html>"
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert sal_min is None
        assert sal_max is None

    def test_yr_suffix(self):
        html = '<div class="salary-main-rail__data-body">$120,000 - $150,000/yr</div>'
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert period == "yearly"

    def test_vietnamese_period(self):
        html = '<div class="salary-main-rail__data-body">₫500.000/giờ</div>'
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert period == "hourly"
        assert currency == "VND"
        assert sal_min == 500_000

    def test_plain_numbers_without_separators(self):
        html = '<div class="salary-main-rail__data-body">₫15000000 - ₫25000000/tháng</div>'
        sal_min, sal_max, currency, period = _parse_salary_structured(html)
        assert sal_min == 15_000_000
        assert sal_max == 25_000_000


# ── _parse_company_logo ──────────────────────────────────────────


class TestParseCompanyLogo:
    def test_extracts_logo_url(self):
        html = '<img class="artdeco-entity-image" data-ghost-url="https://media.licdn.com/logo.png" />'
        assert _parse_company_logo(html) == "https://media.licdn.com/logo.png"

    def test_uses_src_fallback(self):
        html = '<img class="artdeco-entity-image" src="https://media.licdn.com/logo2.png" />'
        assert _parse_company_logo(html) == "https://media.licdn.com/logo2.png"

    def test_no_logo(self):
        html = "<html>no logo</html>"
        assert _parse_company_logo(html) is None


# ── parse_html (integration) ────────────────────────────────────


class TestParseHtml:
    def test_full_parse(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html(
            title="Senior Data Engineer",
            company="Acme Corp",
            location="Ho Chi Minh City, Vietnam",
            salary_display="1,000,000 - 3,000,000 VND/month",
            posted_text="2 days ago",
            applicants_text="25 applicants",
            logo_url="https://media.licdn.com/logo.png",
        )

        detail = parser.parse_html(html, source_job_id="12345")

        assert detail.source == "linkedin"
        assert detail.source_job_id == "12345"
        assert detail.title == "Senior Data Engineer"
        assert detail.company_name == "Acme Corp"
        assert detail.source_url == "https://www.linkedin.com/jobs/view/12345"
        assert detail.parser_version == "linkedin-v1"
        assert detail.salary_min == 1_000_000
        assert detail.salary_max == 3_000_000
        assert detail.salary_currency == "VND"
        assert detail.is_salary_visible is True
        assert detail.salary_period_id == 1  # monthly
        assert detail.job_level == "Senior"
        assert detail.employment_type == "Full-time"
        assert detail.job_function == "Engineering"
        assert detail.num_of_applications == 25
        assert detail.company_logo_url == "https://media.licdn.com/logo.png"
        assert len(detail.locations) == 1
        assert detail.locations[0]["city"] == "Ho Chi Minh City, Vietnam"
        assert detail.is_active is True
        assert detail.is_expired is False

    def test_no_title_raises(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = "<html><body>no title element</body></html>"
        with pytest.raises(LinkedInParseError, match="No title"):
            parser.parse_html(html)

    def test_no_salary(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html(salary_display=None)
        detail = parser.parse_html(html, source_job_id="12345")
        assert detail.salary_min is None
        assert detail.salary_max is None
        assert detail.is_salary_visible is False
        assert detail.pretty_salary is None

    def test_no_criteria(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html(criteria=[])
        detail = parser.parse_html(html, source_job_id="12345")
        assert detail.job_level is None
        assert detail.employment_type is None
        assert detail.job_function is None
        assert detail.industries == []

    def test_hourly_salary_period_id(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html(salary_display="$50 - $80/hr")
        detail = parser.parse_html(html, source_job_id="12345")
        assert detail.salary_period_id == 2  # hourly

    def test_yearly_salary_period_id(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html(salary_display="$120,000 - $150,000/year")
        detail = parser.parse_html(html, source_job_id="12345")
        assert detail.salary_period_id == 3  # yearly

    def test_no_source_job_id_url(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html()
        detail = parser.parse_html(html, source_job_id=None)
        assert detail.source_url is None

    def test_industries_parsed(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html(criteria=["Mid-Senior", "Contract", "Data", "Tech Industry"])
        detail = parser.parse_html(html, source_job_id="12345")
        assert detail.industries == [{"name": "Tech Industry"}]

    def test_description_html_stripped(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html(description="<p>Build <b>data</b> pipelines</p>")
        detail = parser.parse_html(html, source_job_id="12345")
        assert "<p>" not in (detail.job_description_text or "")
        assert "Build" in (detail.job_description_text or "")

    def test_no_description(self):
        parser = LinkedInDetailParser.__new__(LinkedInDetailParser)
        html = make_html()
        html = html.replace('<div class="show-more-less-html__markup">', '<div class="other">')
        detail = parser.parse_html(html, source_job_id="12345")
        assert detail.job_description_text is None
