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
