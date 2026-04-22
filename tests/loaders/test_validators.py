import pytest

from src.loaders.validators import (
    ALLOWED_FUNCTION_IDS,
    validate,
    validate_business_rules,
    validate_focus,
)


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


# --- validate_focus ---

def test_focus_pass_de_ai():
    assert validate_focus(_valid_de_payload()) is None


def test_focus_reject_other_function():
    p = _valid_de_payload(
        job_function={"parentId": 5, "children": [{"id": 9, "name": "Software Developer"}]}
    )
    result = validate_focus(p)
    assert result is not None
    assert result[0] == "OUT_OF_FOCUS"
    assert "Software Developer" in result[1]


def test_focus_reject_missing_job_function():
    p = _valid_de_payload(job_function=None)
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"


def test_focus_reject_empty_children():
    p = _valid_de_payload(job_function={"parentId": 5, "parentName": "IT", "children": []})
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"
    assert "parentId=5" in result[1]


def test_focus_custom_allowed_ids():
    p = _valid_de_payload(
        job_function={"children": [{"id": 99, "name": "Custom"}]}
    )
    assert validate_focus(p, allowed_ids={99}) is None
    assert validate_focus(p, allowed_ids={27}) is not None


def test_focus_handles_non_dict_function():
    p = _valid_de_payload(job_function="just a string")
    result = validate_focus(p)
    assert result[0] == "OUT_OF_FOCUS"
    assert "str" in result[1]


# --- validate_business_rules ---

def test_rules_pass_valid():
    assert validate_business_rules(_valid_de_payload()) is None


def test_rules_reject_missing_title():
    result = validate_business_rules(_valid_de_payload(title=None))
    assert result[0] == "MISSING_TITLE"


def test_rules_reject_missing_company():
    result = validate_business_rules(_valid_de_payload(company_name=""))
    assert result[0] == "MISSING_COMPANY"


def test_rules_reject_bad_salary_range():
    p = _valid_de_payload(
        is_salary_visible=True, salary_min=50_000_000, salary_max=10_000_000
    )
    result = validate_business_rules(p)
    assert result[0] == "BAD_SALARY_RANGE"


def test_rules_allow_bad_salary_when_hidden():
    """If not visible, we don't trust min/max anyway — don't reject."""
    p = _valid_de_payload(
        is_salary_visible=False, salary_min=50_000_000, salary_max=10_000_000
    )
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


# --- validate (orchestrator) ---

def test_validate_hard_rules_win_over_focus():
    """Non-DE/AI job missing title → reports MISSING_TITLE, not OUT_OF_FOCUS."""
    p = _valid_de_payload(
        title=None,
        job_function={"children": [{"id": 99, "name": "Other"}]},
    )
    result = validate(p)
    assert result[0] == "MISSING_TITLE"


def test_validate_focus_catches_when_rules_pass():
    p = _valid_de_payload(
        job_function={"children": [{"id": 99, "name": "Other"}]}
    )
    result = validate(p)
    assert result[0] == "OUT_OF_FOCUS"


def test_validate_happy_path():
    assert validate(_valid_de_payload()) is None


def test_default_allowed_includes_27():
    assert 27 in ALLOWED_FUNCTION_IDS
