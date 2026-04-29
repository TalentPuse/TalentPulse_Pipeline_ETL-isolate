import pytest

from src.loaders.validators import (
    validate,
    validate_business_rules,
    validate_focus,
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
