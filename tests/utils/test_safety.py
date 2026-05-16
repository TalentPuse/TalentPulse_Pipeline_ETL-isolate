"""Tests for kill switch module — env var read/write."""
import os

from src.utils import safety


class TestIsKilled:
    def test_default_is_not_killed(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        assert safety.is_killed() is False

    def test_set_to_1_is_killed(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_KILL_SWITCH", "1")
        assert safety.is_killed() is True

    def test_set_to_0_is_not_killed(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_KILL_SWITCH", "0")
        assert safety.is_killed() is False

    def test_set_to_random_string_is_not_killed(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_KILL_SWITCH", "true")
        assert safety.is_killed() is False


class TestTrigger:
    def test_trigger_sets_env_var(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        safety.trigger()
        assert os.environ.get("CRAWLER_KILL_SWITCH") == "1"

    def test_trigger_then_is_killed(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        safety.trigger()
        assert safety.is_killed() is True
        # cleanup
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    def test_trigger_overwrites_previous(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_KILL_SWITCH", "0")
        assert safety.is_killed() is False
        safety.trigger()
        assert safety.is_killed() is True
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
