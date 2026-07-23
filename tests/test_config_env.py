from unittest import mock
"""Thorough tests for env-var-driven configuration.

Covers: VNW_KEYWORDS, ITVIEC_KEYWORDS, ALLOWED_FUNCTION_IDS,
FOCUS_KEYWORDS, SKIP_FOCUS_SOURCES — parsing, defaults, edge cases.
"""
import os
import importlib
import pytest

import src.utils.config as config_mod


def _reload_config(env_overrides: dict):
    """Reload config with ONLY the given env vars set, and return a fresh Config.

    The environment is cleared, not merely topped up, and `load_dotenv` is stubbed
    out for the duration. Without both, config.py's module-level `load_dotenv()`
    re-reads the developer's real `.env` on every reload and injects it into
    os.environ — so a test asserting "the default value" instead sees whatever is
    in that file. CI has no `.env`, so it passes there and fails only on the machine
    of whoever actually has the project running: the worst possible place for a test
    to be wrong.
    """
    # Patch dotenv at the SOURCE, not on config_mod: reloading config re-executes
    # `from dotenv import load_dotenv`, which rebinds the name and overwrites a
    # patch applied to the module attribute.
    with mock.patch.dict(os.environ, env_overrides, clear=True), \
         mock.patch("dotenv.load_dotenv", lambda *a, **k: None):
        importlib.reload(config_mod)
        return config_mod.config
    # Deliberately NOT reloading again on the way out: several tests below call
    # into validators, which read the module-level `config` singleton, and expect
    # to see the config this helper just built. The autouse `_restore_config`
    # fixture puts the module back after each test.


@pytest.fixture(autouse=True)
def _restore_config():
    """Re-import config module after every test to undo side-effects."""
    yield
    for key in (
        "VNW_KEYWORDS", "ITVIEC_KEYWORDS", "ALLOWED_FUNCTION_IDS",
        "FOCUS_KEYWORDS", "SKIP_FOCUS_SOURCES",
    ):
        os.environ.pop(key, None)
    importlib.reload(config_mod)


# ===== VNW_KEYWORDS =====

class TestVNWKeywords:
    def test_default(self):
        cfg = _reload_config({})
        assert cfg.VNW_KEYWORDS == [
            "Data Engineer", "AI Engineer", "Business Analyst",
            "Business Development", "Technical Sales",
        ]

    def test_custom_single(self):
        cfg = _reload_config({"VNW_KEYWORDS": "Backend Developer"})
        assert cfg.VNW_KEYWORDS == ["Backend Developer"]

    def test_custom_multiple(self):
        cfg = _reload_config({"VNW_KEYWORDS": "Data Engineer,ML Engineer,DevOps"})
        assert cfg.VNW_KEYWORDS == ["Data Engineer", "ML Engineer", "DevOps"]

    def test_strips_whitespace(self):
        cfg = _reload_config({"VNW_KEYWORDS": "  Data Engineer , AI Engineer  "})
        assert cfg.VNW_KEYWORDS == ["Data Engineer", "AI Engineer"]

    def test_trailing_comma_ignored(self):
        cfg = _reload_config({"VNW_KEYWORDS": "Data Engineer,"})
        assert cfg.VNW_KEYWORDS == ["Data Engineer"]

    def test_empty_string_yields_empty_list(self):
        cfg = _reload_config({"VNW_KEYWORDS": ""})
        assert cfg.VNW_KEYWORDS == []

    def test_only_commas_yields_empty_list(self):
        cfg = _reload_config({"VNW_KEYWORDS": ",,,"})
        assert cfg.VNW_KEYWORDS == []

    def test_preserves_order(self):
        cfg = _reload_config({"VNW_KEYWORDS": "C,B,A"})
        assert cfg.VNW_KEYWORDS == ["C", "B", "A"]

    def test_is_list(self):
        cfg = _reload_config({})
        assert isinstance(cfg.VNW_KEYWORDS, list)


# ===== ITVIEC_KEYWORDS =====

class TestITviecKeywords:
    def test_default(self):
        cfg = _reload_config({})
        assert cfg.ITVIEC_KEYWORDS == ["data-engineer", "ai-engineer", "data-analyst"]

    def test_custom(self):
        cfg = _reload_config({"ITVIEC_KEYWORDS": "devops,backend"})
        assert cfg.ITVIEC_KEYWORDS == ["devops", "backend"]

    def test_strips_whitespace(self):
        cfg = _reload_config({"ITVIEC_KEYWORDS": " data-engineer , ai-engineer "})
        assert cfg.ITVIEC_KEYWORDS == ["data-engineer", "ai-engineer"]

    def test_empty_string_yields_empty_list(self):
        cfg = _reload_config({"ITVIEC_KEYWORDS": ""})
        assert cfg.ITVIEC_KEYWORDS == []

    def test_is_list(self):
        cfg = _reload_config({})
        assert isinstance(cfg.ITVIEC_KEYWORDS, list)


# ===== ALLOWED_FUNCTION_IDS =====

class TestAllowedFunctionIDs:
    def test_default_contains_all_ids(self):
        cfg = _reload_config({})
        assert cfg.ALLOWED_FUNCTION_IDS == {25, 27, 129, 130}

    def test_custom_single(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "42"})
        assert cfg.ALLOWED_FUNCTION_IDS == {42}

    def test_custom_multiple(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "27,42,99"})
        assert cfg.ALLOWED_FUNCTION_IDS == {27, 42, 99}

    def test_strips_whitespace(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": " 27 , 42 "})
        assert cfg.ALLOWED_FUNCTION_IDS == {27, 42}

    def test_trailing_comma_ignored(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "27,"})
        assert cfg.ALLOWED_FUNCTION_IDS == {27}

    def test_duplicates_collapsed(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "27,27,27"})
        assert cfg.ALLOWED_FUNCTION_IDS == {27}

    def test_is_set_of_int(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "27,42"})
        assert isinstance(cfg.ALLOWED_FUNCTION_IDS, set)
        for item in cfg.ALLOWED_FUNCTION_IDS:
            assert isinstance(item, int)

    def test_invalid_non_numeric_raises(self):
        with pytest.raises(ValueError):
            _reload_config({"ALLOWED_FUNCTION_IDS": "abc"})

    def test_empty_string_yields_empty_set(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": ""})
        assert cfg.ALLOWED_FUNCTION_IDS == set()

    def test_target_job_function_ids_synced(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "27,42"})
        assert set(cfg.TARGET_JOB_FUNCTION_IDS) == {27, 42}


# ===== FOCUS_KEYWORDS =====

class TestFocusKeywords:
    def test_default_contains_expected(self):
        cfg = _reload_config({})
        expected = {
            "data engineer", "data analyst", "ai", "machine learning",
            "data science", "data scientist", "big data", "analytics",
            "business development", "business analyst", "technical sales",
        }
        assert cfg.FOCUS_KEYWORDS == expected

    def test_custom(self):
        cfg = _reload_config({"FOCUS_KEYWORDS": "devops,cloud,sre"})
        assert cfg.FOCUS_KEYWORDS == {"devops", "cloud", "sre"}

    def test_lowercased(self):
        cfg = _reload_config({"FOCUS_KEYWORDS": "Data Engineer,AI"})
        assert cfg.FOCUS_KEYWORDS == {"data engineer", "ai"}

    def test_strips_whitespace(self):
        cfg = _reload_config({"FOCUS_KEYWORDS": " data engineer , ai "})
        assert cfg.FOCUS_KEYWORDS == {"data engineer", "ai"}

    def test_trailing_comma_ignored(self):
        cfg = _reload_config({"FOCUS_KEYWORDS": "ai,"})
        assert cfg.FOCUS_KEYWORDS == {"ai"}

    def test_empty_string_yields_empty_set(self):
        cfg = _reload_config({"FOCUS_KEYWORDS": ""})
        assert cfg.FOCUS_KEYWORDS == set()

    def test_duplicates_collapsed(self):
        cfg = _reload_config({"FOCUS_KEYWORDS": "ai,AI,Ai"})
        assert cfg.FOCUS_KEYWORDS == {"ai"}

    def test_is_set_of_str(self):
        cfg = _reload_config({})
        assert isinstance(cfg.FOCUS_KEYWORDS, set)
        for item in cfg.FOCUS_KEYWORDS:
            assert isinstance(item, str)


# ===== SKIP_FOCUS_SOURCES =====

class TestSkipFocusSources:
    def test_default_contains_itviec_and_topcv(self):
        # Both keyword-search sources skip focus: their job_function is an
        # industry string that would otherwise fail validate_focus.
        cfg = _reload_config({})
        assert cfg.SKIP_FOCUS_SOURCES == {"itviec", "topcv"}

    def test_custom_multiple(self):
        cfg = _reload_config({"SKIP_FOCUS_SOURCES": "itviec,topcv,linkedin"})
        assert cfg.SKIP_FOCUS_SOURCES == {"itviec", "topcv", "linkedin"}

    def test_strips_whitespace(self):
        cfg = _reload_config({"SKIP_FOCUS_SOURCES": " itviec , topcv "})
        assert cfg.SKIP_FOCUS_SOURCES == {"itviec", "topcv"}

    def test_empty_string_yields_empty_set(self):
        cfg = _reload_config({"SKIP_FOCUS_SOURCES": ""})
        assert cfg.SKIP_FOCUS_SOURCES == set()

    def test_is_set_of_str(self):
        cfg = _reload_config({})
        assert isinstance(cfg.SKIP_FOCUS_SOURCES, set)


# ===== Validators respect config (integration) =====

class TestValidatorsUseConfig:
    """Verify validators read live config values, not stale snapshots."""

    def test_validate_focus_uses_config_allowed_ids(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "99"})
        from src.loaders.validators import validate_focus
        importlib.reload(__import__("src.loaders.validators", fromlist=["validate_focus"]))
        from src.loaders.validators import validate_focus

        payload = {
            "job_function": {"children": [{"id": 99, "name": "Custom"}]},
        }
        assert validate_focus(payload) is None

        payload_reject = {
            "job_function": {"children": [{"id": 27, "name": "DE"}]},
        }
        result = validate_focus(payload_reject)
        assert result is not None
        assert result[0] == "OUT_OF_FOCUS"

    def test_validate_focus_uses_config_keywords(self):
        cfg = _reload_config({"FOCUS_KEYWORDS": "quantum computing"})
        importlib.reload(__import__("src.loaders.validators", fromlist=["validate_focus"]))
        from src.loaders.validators import validate_focus

        assert validate_focus({"job_function": "Quantum Computing Lead"}) is None
        result = validate_focus({"job_function": "Data Engineer"})
        assert result is not None
        assert result[0] == "OUT_OF_FOCUS"

    def test_validate_skip_focus_sources_from_config(self):
        cfg = _reload_config({"SKIP_FOCUS_SOURCES": "topcv"})
        importlib.reload(__import__("src.loaders.validators", fromlist=["validate"]))
        from src.loaders.validators import validate

        payload = {
            "source": "topcv",
            "title": "HR Manager",
            "company_name": "ACME",
            "job_function": {"children": [{"id": 999, "name": "HR"}]},
        }
        assert validate(payload) is None

    def test_validate_itviec_no_longer_skipped_when_removed(self):
        cfg = _reload_config({"SKIP_FOCUS_SOURCES": ""})
        importlib.reload(__import__("src.loaders.validators", fromlist=["validate"]))
        from src.loaders.validators import validate

        payload = {
            "source": "itviec",
            "title": "HR Manager",
            "company_name": "ACME",
            "job_function": {"children": [{"id": 999, "name": "HR"}]},
        }
        result = validate(payload)
        assert result is not None
        assert result[0] == "OUT_OF_FOCUS"


# ===== Cross-config consistency =====

class TestConfigConsistency:
    def test_target_job_function_ids_matches_allowed(self):
        cfg = _reload_config({})
        assert set(cfg.TARGET_JOB_FUNCTION_IDS) == cfg.ALLOWED_FUNCTION_IDS

    def test_focus_keywords_all_lowercase(self):
        cfg = _reload_config({})
        for kw in cfg.FOCUS_KEYWORDS:
            assert kw == kw.lower(), f"Keyword '{kw}' is not lowercase"

    def test_docker_compose_defaults_match_config_defaults(self):
        """Defaults in config.py should match docker-compose.yml x-worker-env."""
        cfg = _reload_config({})
        assert cfg.VNW_KEYWORDS == [
            "Data Engineer", "AI Engineer", "Business Analyst",
            "Business Development", "Technical Sales",
        ]
        assert cfg.ITVIEC_KEYWORDS == ["data-engineer", "ai-engineer", "data-analyst"]
        assert cfg.ALLOWED_FUNCTION_IDS == {25, 27, 129, 130}
        assert "itviec" in cfg.SKIP_FOCUS_SOURCES


# ===== LINKEDIN_TITLE_KEYWORDS =====

class TestLinkedInTitleKeywords:
    def test_default_matches_linkedin_keywords(self):
        cfg = _reload_config({})
        expected = {"data engineer", "data analyst", "ai engineer", "data scientist", "business analyst"}
        assert cfg.LINKEDIN_TITLE_KEYWORDS == expected

    def test_custom_override(self):
        cfg = _reload_config({"LINKEDIN_TITLE_KEYWORDS": "ML Engineer,DevOps"})
        assert cfg.LINKEDIN_TITLE_KEYWORDS == {"ml engineer", "devops"}

    def test_fallback_to_linkedin_keywords(self):
        cfg = _reload_config({"LINKEDIN_KEYWORDS": "Cloud Architect,SRE"})
        assert cfg.LINKEDIN_TITLE_KEYWORDS == {"cloud architect", "sre"}

    def test_all_lowercase(self):
        cfg = _reload_config({})
        for kw in cfg.LINKEDIN_TITLE_KEYWORDS:
            assert kw == kw.lower()
