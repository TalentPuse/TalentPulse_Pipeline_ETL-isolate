"""Loader-side validation — pure functions, no I/O.

Called before upsert. Returns (reason_code, detail) if the payload should be
rejected, else None. Rejects are written to raw.job_detail_rejects for audit.

Design: first-failure-wins. Hard business rules checked before focus filter
so that 'broken' rows are always quarantined regardless of whether they are
on-topic.
"""
from __future__ import annotations

from typing import Optional

# VNW jobFunctionV3Id = 27 is "Data Engineer/Data Analyst/AI"
ALLOWED_FUNCTION_IDS: set[int] = {27}

ValidationResult = Optional[tuple[str, str]]


FOCUS_KEYWORDS: set[str] = {
    "data engineer", "data analyst", "ai", "machine learning",
    "data science", "data scientist", "big data", "analytics",
}

SKIP_FOCUS_SOURCES: set[str] = {"itviec"}


def validate_focus(
    payload: dict, allowed_ids: set[int] | None = None
) -> ValidationResult:
    """Reject if the job's function is not in `allowed_ids`.

    Handles two shapes:
    - dict with children[*].id  (raw API structure)
    - str                       (parser-flattened display name)
    """
    allowed = allowed_ids or ALLOWED_FUNCTION_IDS
    jf = payload.get("job_function")

    if jf is None:
        return None

    if isinstance(jf, str):
        lower = jf.lower()
        if any(kw in lower for kw in FOCUS_KEYWORDS):
            return None
        return ("OUT_OF_FOCUS", f"function='{jf}' (string, no keyword match)")

    if not isinstance(jf, dict):
        return ("OUT_OF_FOCUS", f"job_function is not a dict: {type(jf).__name__}")

    children = jf.get("children") or []
    if isinstance(children, list):
        for c in children:
            if isinstance(c, dict) and c.get("id") in allowed:
                return None

    parent_id = jf.get("parentId")
    fn_name = None
    if isinstance(children, list) and children and isinstance(children[0], dict):
        fn_name = children[0].get("name")
    fn_name = fn_name or jf.get("parentName") or "?"
    return ("OUT_OF_FOCUS", f"function='{fn_name}' parentId={parent_id}")


def validate_business_rules(payload: dict) -> ValidationResult:
    """Hard rules that mean the row is broken regardless of topic."""
    if not payload.get("title"):
        return ("MISSING_TITLE", "")
    if not payload.get("company_name"):
        return ("MISSING_COMPANY", "")

    smin = payload.get("salary_min")
    smax = payload.get("salary_max")
    if (
        payload.get("is_salary_visible")
        and smin is not None
        and smax is not None
        and smin > smax
    ):
        return ("BAD_SALARY_RANGE", f"min={smin} > max={smax}")

    posted = payload.get("posted_at")
    expired = payload.get("expired_at")
    if posted and expired and expired < posted:
        return ("BAD_DATE_RANGE", f"expired_at={expired} < posted_at={posted}")

    return None


def validate(
    payload: dict, allowed_ids: set[int] | None = None
) -> ValidationResult:
    """Run all validators. Hard rules first, then focus filter."""
    result = validate_business_rules(payload)
    if result:
        return result
    if payload.get("source") in SKIP_FOCUS_SOURCES:
        return None
    return validate_focus(payload, allowed_ids)
