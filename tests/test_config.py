import pytest
from src.utils.config import Config

def test_get_db_uri(monkeypatch):
    """
    Test whether the PostgreSQL DB URI is generated correctly based on config attributes.
    """
    # Override class attributes dynamically for testing
    monkeypatch.setattr(Config, "DB_USER", "test_user")
    monkeypatch.setattr(Config, "DB_PASSWORD", "test_pass")
    monkeypatch.setattr(Config, "DB_HOST", "testhost")
    monkeypatch.setattr(Config, "DB_PORT", "5432")
    monkeypatch.setattr(Config, "DB_NAME", "test_db")

    uri = Config.get_db_uri()

    expected_uri = "postgresql://test_user:test_pass@testhost:5432/test_db"
    assert uri == expected_uri, f"Expected {expected_uri}, got {uri}"


# ── Crawl keywords are now hardcoded (no env override) ──────────────────────
# See src/utils/config.py: CRAWL_KEYWORDS is the single source of truth;
# VNW/LINKEDIN use the text form, ITVIEC/TOPCV use the slugified form.

CRAWL_KEYWORDS = [
    "Data Engineer", "AI Engineer", "Data Analyst", "Data Scientist",
    "Machine Learning Engineer", "MLOps Engineer", "Business Intelligence",
    "Business Analyst", "Software Engineer", "DevOps Engineer",
    "Cloud Engineer", "Cyber Security", "SOC Engineer", "Network Engineer",
    "System Engineer", "QA/QC Engineer", "Technical Support",
    "Technical Sales", "Business Development", "Product Manager",
    "Project Manager", "UI/UX Designer",
]

ITVIEC_SLUGS = [
    "data-engineer", "ai-engineer", "data-analyst", "data-scientist",
    "machine-learning-engineer", "mlops-engineer", "business-intelligence",
    "business-analyst", "software-engineer", "devops-engineer",
    "cloud-engineer", "cyber-security", "soc-engineer", "network-engineer",
    "system-engineer", "qa-qc-engineer", "technical-support",
    "technical-sales", "business-development", "product-manager",
    "project-manager", "ui-ux-designer",
]


def test_crawl_keywords_hardcoded():
    from src.utils.config import config
    assert config.CRAWL_KEYWORDS == CRAWL_KEYWORDS


def test_vnw_keywords_equals_crawl_keywords():
    from src.utils.config import config
    assert config.VNW_KEYWORDS == config.CRAWL_KEYWORDS


def test_linkedin_keywords_equals_crawl_keywords():
    from src.utils.config import config
    assert config.LINKEDIN_KEYWORDS == config.CRAWL_KEYWORDS


def test_itviec_keywords_default():
    from src.utils.config import config
    assert config.ITVIEC_KEYWORDS == ITVIEC_SLUGS


def test_topcv_keywords_default():
    from src.utils.config import config
    assert config.TOPCV_KEYWORDS == ITVIEC_SLUGS


def test_topcv_keywords_equals_itviec_keywords():
    from src.utils.config import config
    assert config.TOPCV_KEYWORDS == config.ITVIEC_KEYWORDS


def test_allowed_function_ids_empty():
    """VNW crawls by keyword only now; no server-side function-id filter."""
    from src.utils.config import config
    assert config.ALLOWED_FUNCTION_IDS == set()


def test_target_job_function_ids_empty():
    from src.utils.config import config
    assert config.TARGET_JOB_FUNCTION_IDS == []
