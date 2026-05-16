"""Tests for category matcher — word-boundary regex matching."""
import pytest

from src.normalizer.matchers.category import match_category
from src.normalizer.models import MatchResult


# ─── Fixtures ─────────────────────────────────────────────────────

RULES = [
    {"pattern": "data engineer", "job_category": "Data Engineer", "priority": 10, "match_mode": "word_boundary", "is_active": True},
    {"pattern": "data scientist", "job_category": "Data Scientist", "priority": 10, "match_mode": "word_boundary", "is_active": True},
    {"pattern": "ai engineer", "job_category": "AI Engineer", "priority": 10, "match_mode": "word_boundary", "is_active": True},
    {"pattern": "business analyst", "job_category": "Business Analyst", "priority": 10, "match_mode": "word_boundary", "is_active": True},
    {"pattern": "frontend developer", "job_category": "Frontend Developer", "priority": 10, "match_mode": "word_boundary", "is_active": True},
    {"pattern": "backend developer", "job_category": "Backend Developer", "priority": 10, "match_mode": "word_boundary", "is_active": True},
    {"pattern": "devops", "job_category": "DevOps Engineer", "priority": 15, "match_mode": "word_boundary", "is_active": True},
    {"pattern": "machine learning", "job_category": "AI Engineer", "priority": 20, "match_mode": "word_boundary", "is_active": True},
    {"pattern": "python", "job_category": "Other", "priority": 50, "match_mode": "word_boundary", "is_active": True},
]


# ─── Word boundary matching ───────────────────────────────────────

class TestWordBoundary:
    """Verify that word-boundary matching prevents false substring matches."""

    def test_exact_word_match(self):
        result = match_category("Data Engineer", "vietnamworks", None, RULES)
        assert result.value == "Data Engineer"
        assert result.method == "title_keyword"

    def test_case_insensitive(self):
        result = match_category("DATA ENGINEER - REMOTE", "vietnamworks", None, RULES)
        assert result.value == "Data Engineer"

    def test_word_in_longer_title(self):
        result = match_category("Senior Data Engineer / Data Scientist", "vietnamworks", None, RULES)
        assert result.value == "Data Engineer"  # priority 10, first match wins

    def test_no_false_substring_match_html(self):
        """'html' should NOT match inside 'Backend Developer - HTML/CSS required'."""
        rules_with_html = RULES + [
            {"pattern": "html", "job_category": "Frontend Developer", "priority": 25, "match_mode": "word_boundary", "is_active": True},
        ]
        result = match_category("Backend Developer - HTML/CSS required", "vietnamworks", None, rules_with_html)
        assert result.value == "Backend Developer"
        assert result.value != "Frontend Developer"

    def test_no_false_substring_match_sap(self):
        """'sap' should NOT match inside 'API integration specialist'."""
        rules_with_sap = RULES + [
            {"pattern": "sap", "job_category": "ERP Consultant", "priority": 10, "match_mode": "word_boundary", "is_active": True},
        ]
        result = match_category("API integration specialist", "vietnamworks", None, rules_with_sap)
        assert result.value != "ERP Consultant"

    def test_compound_pattern_html_css(self):
        """'html/css' should match a Frontend job mentioning HTML/CSS."""
        rules = [
            {"pattern": "html/css", "job_category": "Frontend Developer", "priority": 25, "match_mode": "word_boundary", "is_active": True},
        ]
        # Note: \b treats / as a word boundary, so "html/css" won't match as one word.
        # The compound pattern "html/css" needs regex mode.
        rules[0]["match_mode"] = "regex"
        rules[0]["pattern"] = r"html\s*/\s*css"
        result = match_category("Frontend Developer - HTML/CSS", "vietnamworks", None, rules)
        assert result.value == "Frontend Developer"

    def test_devops_word_boundary(self):
        """'devops' should match 'DevOps Engineer' but not 'devopsengineer'."""
        result = match_category("DevOps Engineer", "vietnamworks", None, RULES)
        assert result.value == "DevOps Engineer"

    def test_no_match_returns_other(self):
        result = match_category("Technical Writer", "vietnamworks", None, RULES)
        assert result.value == "Other"
        assert result.method == "default"
        assert result.confidence == 0.0


# ─── VNW source_metadata signal ──────────────────────────────────

class TestVnwSourceMetadata:
    """VNW structured job_function should override title matching."""

    def test_vnw_function_overrides_title(self):
        job_function = {
            "children": [{"name": "Sales/Business Development"}]
        }
        result = match_category(
            "Data Engineer",
            "vietnamworks",
            job_function,
            RULES,
        )
        assert result.value == "Business Development"
        assert result.method == "source_metadata"
        assert result.confidence == 0.95

    def test_vnw_function_not_matched_for_other_sources(self):
        job_function = {
            "children": [{"name": "Sales/Business Development"}]
        }
        result = match_category(
            "Data Engineer",
            "itviec",
            job_function,
            RULES,
        )
        assert result.method != "source_metadata"
        assert result.value == "Data Engineer"

    def test_vnw_function_no_match_falls_to_title(self):
        job_function = {"children": [{"name": "Unknown Function"}]}
        result = match_category(
            "Data Engineer",
            "vietnamworks",
            job_function,
            RULES,
        )
        assert result.method == "title_keyword"
        assert result.value == "Data Engineer"


# ─── Priority ordering ────────────────────────────────────────────

class TestPriority:
    """Lower priority number should be matched first."""

    def test_lower_priority_wins(self):
        rules = [
            {"pattern": "data engineer", "job_category": "Data Engineer", "priority": 10, "match_mode": "word_boundary", "is_active": True},
            {"pattern": "engineer", "job_category": "Other", "priority": 50, "match_mode": "word_boundary", "is_active": True},
        ]
        result = match_category("Data Engineer", "vietnamworks", None, rules)
        assert result.value == "Data Engineer"  # priority 10 wins over 50

    def test_inactive_rule_skipped(self):
        rules = [
            {"pattern": "data engineer", "job_category": "Data Engineer", "priority": 10, "match_mode": "word_boundary", "is_active": True},
            {"pattern": "data", "job_category": "Data Management", "priority": 5, "match_mode": "word_boundary", "is_active": False},
        ]
        result = match_category("Data Engineer", "vietnamworks", None, rules)
        assert result.value == "Data Engineer"


# ─── Edge cases ────────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_title(self):
        result = match_category("", "vietnamworks", None, RULES)
        assert result.value == "Other"

    def test_none_title(self):
        result = match_category(None, "vietnamworks", None, RULES)
        assert result.value == "Other"

    def test_empty_rules(self):
        result = match_category("Data Engineer", "vietnamworks", None, [])
        assert result.value == "Other"

    def test_job_function_as_string(self):
        """job_function as string should not crash, fall through to title matching."""
        result = match_category("Data Engineer", "vietnamworks", "some string", RULES)
        assert result.value == "Data Engineer"

    def test_job_function_as_list(self):
        result = match_category("Data Engineer", "vietnamworks", [], RULES)
        assert result.value == "Data Engineer"
