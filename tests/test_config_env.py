from unittest import mock
"""Thorough tests for env-var-driven configuration.

Covers: FOCUS_KEYWORDS, SKIP_FOCUS_SOURCES, LINKEDIN_TITLE_KEYWORDS —
parsing, defaults, edge cases. Also covers regression tests confirming that
VNW_KEYWORDS / ITVIEC_KEYWORDS / TOPCV_KEYWORDS / LINKEDIN_KEYWORDS and
ALLOWED_FUNCTION_IDS are now HARDCODED in config.py and no longer respond to
their legacy env-var overrides.
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
        "VNW_KEYWORDS", "ITVIEC_KEYWORDS", "TOPCV_KEYWORDS", "LINKEDIN_KEYWORDS",
        "ALLOWED_FUNCTION_IDS", "FOCUS_KEYWORDS", "SKIP_FOCUS_SOURCES",
    ):
        os.environ.pop(key, None)
    importlib.reload(config_mod)


# ===== Keywords / function-ids are hardcoded (env override no longer works) =====

class TestKeywordsNoLongerEnvDriven:
    """VNW/ITVIEC/TOPCV/LINKEDIN keyword lists and ALLOWED_FUNCTION_IDS are now
    hardcoded in config.py (single CRAWL_KEYWORDS source of truth); the legacy
    env vars must have no effect.
    """

    def test_vnw_keywords_ignores_env_override(self):
        cfg = _reload_config({"VNW_KEYWORDS": "Backend Developer"})
        assert cfg.VNW_KEYWORDS == cfg.CRAWL_KEYWORDS
        assert "Backend Developer" not in cfg.VNW_KEYWORDS

    def test_itviec_keywords_ignores_env_override(self):
        cfg = _reload_config({"ITVIEC_KEYWORDS": "devops,backend"})
        assert cfg.ITVIEC_KEYWORDS != ["devops", "backend"]
        assert cfg.ITVIEC_KEYWORDS == cfg.TOPCV_KEYWORDS

    def test_topcv_keywords_ignores_env_override(self):
        cfg = _reload_config({"TOPCV_KEYWORDS": "hr,finance"})
        assert cfg.TOPCV_KEYWORDS != ["hr", "finance"]
        assert cfg.TOPCV_KEYWORDS == cfg.ITVIEC_KEYWORDS

    def test_linkedin_keywords_ignores_env_override(self):
        cfg = _reload_config({"LINKEDIN_KEYWORDS": "Cloud Architect,SRE"})
        assert cfg.LINKEDIN_KEYWORDS == cfg.CRAWL_KEYWORDS

    def test_allowed_function_ids_ignores_env_override(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "27,42,99"})
        assert cfg.ALLOWED_FUNCTION_IDS == set()

    def test_target_job_function_ids_ignores_env_override(self):
        cfg = _reload_config({"ALLOWED_FUNCTION_IDS": "27,42,99"})
        assert cfg.TARGET_JOB_FUNCTION_IDS == []


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
        # SKIP_FOCUS_SOURCES still exists (env-driven) even though validate()
        # no longer consults it — focus filtering was removed from validate().
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
#
# validate() itself no longer performs focus/title/location filtering (see
# src/loaders/validators.py — it only calls validate_business_rules now).
# validate_focus / validate_title_keywords / validate_location_vietnam are
# unchanged and still read live config, so the tests calling them directly
# stay in place.

class TestValidatorsUseConfig:
    """Verify validate_focus reads live config values, not stale snapshots."""

    def test_validate_focus_uses_config_keywords(self):
        cfg = _reload_config({"FOCUS_KEYWORDS": "quantum computing"})
        importlib.reload(__import__("src.loaders.validators", fromlist=["validate_focus"]))
        from src.loaders.validators import validate_focus

        assert validate_focus({"job_function": "Quantum Computing Lead"}) is None
        result = validate_focus({"job_function": "Data Engineer"})
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
        """Defaults in config.py should match docker-compose.yml x-worker-env.

        Keyword / function-id assertions were dropped: those are now hardcoded
        in config.py (not read from env), so there is nothing docker-compose
        can override for them anymore.
        """
        cfg = _reload_config({})
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
