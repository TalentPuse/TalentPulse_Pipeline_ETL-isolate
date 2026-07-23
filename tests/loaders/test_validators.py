import pytest

from src.loaders.validators import (
    validate,
    validate_business_rules,
    validate_focus,
    validate_title_keywords,
    validate_location_vietnam,
)
from src.utils.config import config


def _valid_de_payload(**overrides):
    base = {
        "source": "vietnamworks",
        "source_job_id": "1",
        "title": "Senior Data Engineer",
        "company_name": "ACME",
        "job_function": {
            "parentId": 5,
            "parentName": "IT",
            "children": [{"id": 27, "name": "Data Engineer/Data Analyst/AI"}],
        },
    }
    base.update(overrides)
    return base


# ===== validate_focus: dict-shaped job_function (raw API structure) =====

def test_focus_pass_dict_with_allowed_child_id():
    assert validate_focus(_valid_de_payload()) is None


def test_focus_reject_dict_with_wrong_child_id():
    p = _valid_de_payload(
        job_function={"parentId": 5, "children": [{"id": 9, "name": "Software Developer"}]}
    )
    result = validate_focus(p)
    assert result is not None
    assert result[0] == "OUT_OF_FOCUS"
    assert "Software Developer" in result[1]


def test_focus_reject_dict_empty_children():
    p = _valid_de_payload(job_function={"parentId": 5, "parentName": "IT", "children": []})
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"
    assert "parentId=5" in result[1]


def test_focus_dict_multiple_children_one_match():
    p = _valid_de_payload(job_function={
        "parentId": 5,
        "children": [
            {"id": 9, "name": "Software Developer"},
            {"id": 27, "name": "Data Engineer/Data Analyst/AI"},
        ],
    })
    assert validate_focus(p) is None


def test_focus_dict_multiple_children_none_match():
    p = _valid_de_payload(job_function={
        "parentId": 5,
        "children": [
            {"id": 9, "name": "Software Developer"},
            {"id": 10, "name": "QA Engineer"},
        ],
    })
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"
    assert "Software Developer" in result[1]


def test_focus_custom_allowed_ids():
    p = _valid_de_payload(
        job_function={"children": [{"id": 99, "name": "Custom"}]}
    )
    assert validate_focus(p, allowed_ids={99}) is None
    assert validate_focus(p, allowed_ids={27}) is not None


def test_focus_dict_children_none():
    p = _valid_de_payload(job_function={"parentId": 5, "parentName": "IT", "children": None})
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"


def test_focus_dict_child_missing_id_key():
    p = _valid_de_payload(job_function={"children": [{"name": "Missing ID"}]})
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"


def test_focus_dict_uses_parentName_in_detail():
    p = _valid_de_payload(job_function={
        "parentId": 99,
        "parentName": "Finance",
        "children": [],
    })
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"
    assert "Finance" in result[1]


# ===== validate_focus: string-shaped job_function (parser output) =====

def test_focus_pass_string_exact_keyword():
    p = _valid_de_payload(job_function="Data Engineer/Data Analyst/AI")
    assert validate_focus(p) is None


def test_focus_pass_string_case_insensitive():
    p = _valid_de_payload(job_function="DATA ENGINEER")
    assert validate_focus(p) is None


@pytest.mark.parametrize("fn_str", [
    "Data Engineer",
    "Senior Data Analyst",
    "AI Research Scientist",
    "Machine Learning Engineer",
    "Data Science Lead",
    "Data Scientist",
    "Big Data Platform Engineer",
    "Analytics Engineer",
])
def test_focus_pass_string_all_keywords(fn_str):
    p = _valid_de_payload(job_function=fn_str)
    assert validate_focus(p) is None


def test_focus_reject_string_no_keyword():
    p = _valid_de_payload(job_function="Marketing Manager")
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"
    assert "Marketing Manager" in result[1]
    assert "string" in result[1]


@pytest.mark.parametrize("fn_str", [
    "Account Executive",
    "HR Specialist",
    "Graphic Designer",
    "Sales Representative",
])
def test_focus_reject_string_unrelated_functions(fn_str):
    p = _valid_de_payload(job_function=fn_str)
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"


def test_focus_pass_string_keyword_in_middle():
    p = _valid_de_payload(job_function="Senior Machine Learning Platform Engineer")
    assert validate_focus(p) is None


def test_focus_pass_empty_string():
    p = _valid_de_payload(job_function="")
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"


# ===== validate_focus: None / missing / other types =====

def test_focus_pass_none():
    p = _valid_de_payload(job_function=None)
    assert validate_focus(p) is None


def test_focus_pass_key_missing():
    p = _valid_de_payload()
    del p["job_function"]
    assert validate_focus(p) is None


def test_focus_reject_unexpected_type_list():
    p = _valid_de_payload(job_function=[27])
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"
    assert "list" in result[1]


def test_focus_reject_unexpected_type_int():
    p = _valid_de_payload(job_function=27)
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"
    assert "int" in result[1]


# ===== validate_business_rules =====

def test_rules_pass_valid():
    assert validate_business_rules(_valid_de_payload()) is None


def test_rules_reject_missing_title_none():
    result = validate_business_rules(_valid_de_payload(title=None))
    assert result[0] == "MISSING_TITLE"


def test_rules_reject_missing_title_empty():
    result = validate_business_rules(_valid_de_payload(title=""))
    assert result[0] == "MISSING_TITLE"


def test_rules_reject_missing_company_none():
    result = validate_business_rules(_valid_de_payload(company_name=None))
    assert result[0] == "MISSING_COMPANY"


def test_rules_reject_missing_company_empty():
    result = validate_business_rules(_valid_de_payload(company_name=""))
    assert result[0] == "MISSING_COMPANY"


def test_rules_reject_bad_salary_range():
    p = _valid_de_payload(
        is_salary_visible=True, salary_min=50_000_000, salary_max=10_000_000
    )
    result = validate_business_rules(p)
    assert result[0] == "BAD_SALARY_RANGE"
    assert "50000000" in result[1]


def test_rules_allow_bad_salary_when_hidden():
    p = _valid_de_payload(
        is_salary_visible=False, salary_min=50_000_000, salary_max=10_000_000
    )
    assert validate_business_rules(p) is None


def test_rules_allow_equal_salary():
    p = _valid_de_payload(
        is_salary_visible=True, salary_min=20_000_000, salary_max=20_000_000
    )
    assert validate_business_rules(p) is None


def test_rules_allow_salary_none():
    p = _valid_de_payload(is_salary_visible=True, salary_min=None, salary_max=None)
    assert validate_business_rules(p) is None


def test_rules_allow_salary_min_none():
    p = _valid_de_payload(is_salary_visible=True, salary_min=None, salary_max=30_000_000)
    assert validate_business_rules(p) is None


def test_rules_reject_bad_date_range():
    p = _valid_de_payload(
        posted_at="2026-05-01T00:00:00Z",
        expired_at="2026-04-01T00:00:00Z",
    )
    result = validate_business_rules(p)
    assert result[0] == "BAD_DATE_RANGE"


def test_rules_allow_equal_dates():
    p = _valid_de_payload(
        posted_at="2026-04-01T00:00:00Z",
        expired_at="2026-04-01T00:00:00Z",
    )
    assert validate_business_rules(p) is None


def test_rules_allow_no_dates():
    p = _valid_de_payload(posted_at=None, expired_at=None)
    assert validate_business_rules(p) is None


def test_rules_allow_only_posted_at():
    p = _valid_de_payload(posted_at="2026-04-01T00:00:00Z", expired_at=None)
    assert validate_business_rules(p) is None


def test_rules_allow_only_expired_at():
    p = _valid_de_payload(posted_at=None, expired_at="2026-05-01T00:00:00Z")
    assert validate_business_rules(p) is None


# ===== validate (orchestrator) =====

def test_validate_happy_path_dict_function():
    assert validate(_valid_de_payload()) is None


def test_validate_happy_path_string_function():
    p = _valid_de_payload(job_function="Data Engineer")
    assert validate(p) is None


def test_validate_happy_path_none_function():
    p = _valid_de_payload(job_function=None)
    assert validate(p) is None


def test_validate_hard_rules_win_over_focus():
    p = _valid_de_payload(
        title=None,
        job_function={"children": [{"id": 99, "name": "Other"}]},
    )
    result = validate(p)
    assert result[0] == "MISSING_TITLE"


def test_validate_hard_rules_win_over_string_focus():
    p = _valid_de_payload(title=None, job_function="Marketing Manager")
    result = validate(p)
    assert result[0] == "MISSING_TITLE"


def test_validate_focus_catches_when_rules_pass():
    p = _valid_de_payload(
        job_function={"children": [{"id": 99, "name": "Other"}]}
    )
    result = validate(p)
    assert result[0] == "OUT_OF_FOCUS"


def test_validate_focus_catches_string_when_rules_pass():
    p = _valid_de_payload(job_function="Sales Manager")
    result = validate(p)
    assert result[0] == "OUT_OF_FOCUS"


# ===== Constants =====

def test_default_allowed_includes_27():
    assert 27 in config.ALLOWED_FUNCTION_IDS


def test_focus_keywords_are_lowercase():
    for kw in config.FOCUS_KEYWORDS:
        assert kw == kw.lower()


# ===== validate_title_keywords (LinkedIn title filter) =====

def _linkedin_payload(**overrides):
    base = {
        "source": "linkedin",
        "source_job_id": "123",
        "title": "Senior Data Engineer",
        "company_name": "FPT Software",
        "locations": [{"city": "Ho Chi Minh City, Vietnam"}],
    }
    base.update(overrides)
    return base


class TestValidateTitleKeywords:
    def test_pass_exact_keyword(self):
        p = _linkedin_payload(title="Data Engineer")
        assert validate_title_keywords(p) is None

    def test_pass_keyword_in_longer_title(self):
        p = _linkedin_payload(title="Senior Data Engineer - Remote")
        assert validate_title_keywords(p) is None

    def test_pass_case_insensitive(self):
        p = _linkedin_payload(title="SENIOR DATA ANALYST")
        assert validate_title_keywords(p) is None

    @pytest.mark.parametrize("title", [
        "Data Engineer", "Data Analyst", "AI Engineer",
        "Data Scientist", "Business Analyst",
        "Junior Data Engineer", "Lead Data Scientist",
        "Senior AI Engineer - NLP", "Staff Business Analyst",
    ])
    def test_pass_all_default_keywords(self, title):
        p = _linkedin_payload(title=title)
        assert validate_title_keywords(p) is None

    @pytest.mark.parametrize("title", [
        "Marketing Manager",
        "HR Specialist",
        "Graphic Designer",
        "Customer Insights Specialist",
        "Operations Lead",
        "Software Developer",
        "Sales Representative",
    ])
    def test_reject_unrelated_titles(self, title):
        p = _linkedin_payload(title=title)
        result = validate_title_keywords(p)
        assert result is not None
        assert result[0] == "OUT_OF_FOCUS"
        assert title in result[1]

    def test_pass_none_title_defers_to_missing_title(self):
        p = _linkedin_payload(title=None)
        assert validate_title_keywords(p) is None

    def test_pass_empty_title_defers_to_missing_title(self):
        p = _linkedin_payload(title="")
        assert validate_title_keywords(p) is None


# ===== validate_location_vietnam =====

class TestValidateLocationVietnam:
    def test_pass_hcmc_vietnam(self):
        p = _linkedin_payload(locations=[{"city": "Ho Chi Minh City, Vietnam"}])
        assert validate_location_vietnam(p) is None

    def test_pass_hanoi_vietnam(self):
        p = _linkedin_payload(locations=[{"city": "Hanoi, Vietnam"}])
        assert validate_location_vietnam(p) is None

    def test_pass_vietnam_only(self):
        p = _linkedin_payload(locations=[{"city": "Vietnam"}])
        assert validate_location_vietnam(p) is None

    def test_pass_da_nang(self):
        p = _linkedin_payload(locations=[{"city": "Da Nang"}])
        assert validate_location_vietnam(p) is None

    def test_pass_ho_chi_minh_no_country(self):
        p = _linkedin_payload(locations=[{"city": "Ho Chi Minh"}])
        assert validate_location_vietnam(p) is None

    def test_pass_empty_locations_list(self):
        p = _linkedin_payload(locations=[])
        assert validate_location_vietnam(p) is None

    def test_pass_no_locations_key(self):
        p = _linkedin_payload()
        del p["locations"]
        assert validate_location_vietnam(p) is None

    def test_reject_foreign_city(self):
        p = _linkedin_payload(locations=[{"city": "Singapore"}])
        result = validate_location_vietnam(p)
        assert result is not None
        assert result[0] == "OUT_OF_LOCATION"
        assert "Singapore" in result[1]

    def test_reject_us_city(self):
        p = _linkedin_payload(locations=[{"city": "San Francisco, CA"}])
        result = validate_location_vietnam(p)
        assert result is not None
        assert result[0] == "OUT_OF_LOCATION"

    def test_pass_vietnamese_text(self):
        p = _linkedin_payload(locations=[{"city": "Hồ Chí Minh"}])
        assert validate_location_vietnam(p) is None


# ===== validate (orchestrator) — LinkedIn path =====

class TestValidateLinkedIn:
    def test_linkedin_pass_valid_job(self):
        assert validate(_linkedin_payload()) is None

    def test_linkedin_reject_bad_title(self):
        p = _linkedin_payload(title="Marketing Manager")
        result = validate(p)
        assert result is not None
        assert result[0] == "OUT_OF_FOCUS"

    def test_linkedin_reject_foreign_location(self):
        p = _linkedin_payload(locations=[{"city": "Singapore"}])
        result = validate(p)
        assert result is not None
        assert result[0] == "OUT_OF_LOCATION"

    def test_linkedin_business_rules_checked_first(self):
        p = _linkedin_payload(title=None)
        result = validate(p)
        assert result[0] == "MISSING_TITLE"

    def test_linkedin_does_not_call_focus_filter(self):
        p = _linkedin_payload(
            job_function={"children": [{"id": 999, "name": "Random"}]}
        )
        assert validate(p) is None

    def test_itviec_still_skips_focus(self):
        p = {
            "source": "itviec",
            "title": "HR Manager",
            "company_name": "ACME",
            "job_function": {"children": [{"id": 999, "name": "HR"}]},
        }
        assert validate(p) is None

    def test_vnw_still_uses_focus_filter(self):
        p = _valid_de_payload(
            job_function={"children": [{"id": 999, "name": "Other"}]}
        )
        result = validate(p)
        assert result is not None
        assert result[0] == "OUT_OF_FOCUS"


# ===== topcv: skips focus validation (mirrors itviec) =====
def test_topcv_skips_focus_and_loads_with_industry_job_function(monkeypatch):
    """TopCV job_function is an industry string (no focus keyword); it must still
    pass validate() because topcv is in SKIP_FOCUS_SOURCES — the focused keyword
    search is the filter, not job_function. Monkeypatched so the assertion does
    not depend on the ambient .env value."""
    monkeypatch.setattr(config, "SKIP_FOCUS_SOURCES", {"itviec", "topcv"})
    payload = {
        "source": "topcv",
        "source_job_id": "2114998",
        "title": "Data Engineer (Junior/Middle)",
        "company_name": "VIETTEL DIGITAL SERVICES",
        "job_function": "IT phần mềm, Công nghệ thông tin",  # not a focus keyword
    }
    assert validate(payload) is None


def test_non_skip_source_still_rejects_industry_job_function():
    """Guard: the skip only applies to configured sources, not universally."""
    payload = {
        "source": "someboard",
        "source_job_id": "1",
        "title": "Data Engineer",
        "company_name": "ACME",
        "job_function": "IT phần mềm, Công nghệ thông tin",
    }
    assert validate(payload) == ("OUT_OF_FOCUS", "function='IT phần mềm, Công nghệ thông tin' (string, no keyword match)")
