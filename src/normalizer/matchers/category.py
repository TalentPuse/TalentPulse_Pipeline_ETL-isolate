"""Category matcher — word-boundary regex with VNW source_metadata priority."""
from __future__ import annotations

import re

from src.normalizer.models import MatchResult

# VNW job function children names → category (same mapping as vnw_category CTE in silver_job_detail)
_VNW_FUNCTION_MAP = {
    "Sales/Business Development": "Business Development",
    "Sales Engineer/Technical Sales": "Technical Sales",
    "Business/System Analysis": "Business Analyst",
}


def match_category(
    title: str,
    source: str,
    job_function: dict | list | str | None,
    rules: list[dict],
) -> MatchResult:
    """
    Match job title → category using prioritized word-boundary rules.

    Signal priority:
      1. VNW structured job_function metadata (confidence 0.95)
      2. Word-boundary keyword regex on title (confidence 0.50-0.90)
      3. Default "Other" (confidence 0.0)

    Args:
        title: Job title string.
        source: Source identifier ('vietnamworks', 'itviec', 'linkedin').
        job_function: Raw job_function field (JSONB from parser).
        rules: List of dicts with keys: pattern, job_category, priority, match_mode, is_active.
               Must be pre-sorted by priority ASC.
    """
    title_lower = (title or "").lower()

    # Signal 1: VNW structured job_function metadata
    if source == "vietnamworks" and job_function is not None:
        category = _parse_vnw_job_function(job_function)
        if category:
            return MatchResult(
                value=category,
                method="source_metadata",
                confidence=0.95,
                matched_on=str(job_function)[:100],
            )

    # Signal 2: Word-boundary keyword matching
    for rule in rules:
        if not rule.get("is_active", True):
            continue
        pattern = rule["pattern"]
        mode = rule.get("match_mode", "word_boundary")

        if mode == "word_boundary":
            regex = r"\b" + re.escape(pattern.lower()) + r"\b"
        elif mode == "exact":
            regex = r"^" + re.escape(pattern.lower()) + r"$"
        else:  # 'regex'
            regex = pattern.lower()

        try:
            if re.search(regex, title_lower):
                conf = max(0.50, 1.0 - (rule["priority"] / 100.0))
                return MatchResult(
                    value=rule["job_category"],
                    method="title_keyword",
                    confidence=round(conf, 2),
                    matched_on=pattern,
                )
        except re.error:
            continue

    # Default
    return MatchResult(value="Other", method="default", confidence=0.0)


def _parse_vnw_job_function(job_function: dict | list | str | None) -> str | None:
    """Extract category from VNW structured job_function field."""
    if isinstance(job_function, dict):
        children = job_function.get("children") or []
        if isinstance(children, list) and children and isinstance(children[0], dict):
            name = children[0].get("name", "")
            if name in _VNW_FUNCTION_MAP:
                return _VNW_FUNCTION_MAP[name]
    return None
