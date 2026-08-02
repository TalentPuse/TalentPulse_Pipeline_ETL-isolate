"""Tests for the Threads keyword-search crawler.

All HTTP is mocked. There is no live call anywhere in here: we hold no token,
and the endpoint would only return the authenticated user's own posts until Meta
approves `threads_keyword_search` anyway.
"""
from unittest.mock import MagicMock

import pytest
import requests

from src.crawlers.threads.search import (
    DAILY_QUERY_BUDGET,
    MAX_PAGES_PER_KEYWORD,
    QueryBudget,
    ThreadsAuthError,
    ThreadsConfigError,
    ThreadsQuotaExceeded,
    ThreadsSearchCrawler,
    get_access_token,
)


def _post(post_id: str, text: str = "tuyen Data Engineer") -> dict:
    return {
        "id": post_id,
        "text": text,
        "media_type": "TEXT_POST",
        "permalink": f"https://www.threads.net/@recruiter/post/{post_id}",
        "timestamp": "2026-08-01T09:00:00+0000",
        "username": "recruiter",
        "has_replies": False,
        "is_quote_post": False,
        "is_reply": False,
    }


def _response(data: list[dict], after: str | None = None, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = ""
    payload: dict = {"data": data}
    if after:
        payload["paging"] = {"cursors": {"after": after}}
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _crawler(session, **kwargs) -> ThreadsSearchCrawler:
    """Crawler with archiving off — storage is not what these tests are about."""
    return ThreadsSearchCrawler(
        session=session, minio=None, token="fake-token", archive=False, **kwargs
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("src.crawlers.threads.search.time.sleep", lambda s: None)


# ── credentials ───────────────────────────────────────────────────


class TestAccessToken:
    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
        with pytest.raises(ThreadsConfigError, match="THREADS_ACCESS_TOKEN"):
            get_access_token()

    def test_blank_token_raises(self, monkeypatch):
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "   ")
        with pytest.raises(ThreadsConfigError):
            get_access_token()

    def test_crawler_fails_at_construction_not_mid_run(self, monkeypatch):
        monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
        with pytest.raises(ThreadsConfigError):
            ThreadsSearchCrawler(session=MagicMock(), minio=None, archive=False)

    def test_token_from_env_is_used(self, monkeypatch):
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "env-token")
        c = ThreadsSearchCrawler(session=MagicMock(), minio=None, archive=False)
        assert c.token == "env-token"


# ── pagination ────────────────────────────────────────────────────


class TestPagination:
    def test_follows_after_cursor_until_exhausted(self):
        session = MagicMock()
        session.get.side_effect = [
            _response([_post("1"), _post("2")], after="cur1"),
            _response([_post("3")], after="cur2"),
            _response([]),
        ]
        result = _crawler(session).search_keyword("data engineer")

        assert [p["id"] for p in result["posts"]] == ["1", "2", "3"]
        assert result["pages"] == 3
        # The 2nd/3rd requests must carry the cursor from the previous page.
        assert session.get.call_args_list[1].kwargs["params"]["after"] == "cur1"
        assert session.get.call_args_list[2].kwargs["params"]["after"] == "cur2"

    def test_first_request_has_no_cursor(self):
        session = MagicMock()
        session.get.return_value = _response([_post("1")])
        _crawler(session).search_keyword("data engineer")
        assert "after" not in session.get.call_args.kwargs["params"]

    def test_stops_when_paging_absent(self):
        session = MagicMock()
        session.get.return_value = _response([_post("1")])  # no paging block
        result = _crawler(session).search_keyword("data engineer")
        assert result["pages"] == 1
        assert session.get.call_count == 1

    def test_max_pages_caps_the_walk(self):
        session = MagicMock()
        session.get.return_value = _response([_post("1")], after="always")
        result = _crawler(session).search_keyword("data engineer", max_pages=2)
        assert result["pages"] == 2
        assert session.get.call_count == 2

    def test_endless_cursor_cannot_run_away(self):
        """A cursor that never terminates must not drain the daily budget."""
        session = MagicMock()
        session.get.return_value = _response([_post("1")], after="always")
        result = _crawler(session).search_keyword("data engineer")
        assert result["pages"] == MAX_PAGES_PER_KEYWORD

    def test_deduplicates_across_keywords(self):
        session = MagicMock()
        session.get.side_effect = [
            _response([_post("1"), _post("2")]),
            _response([_post("2"), _post("3")]),
        ]
        out = _crawler(session).search_all(["kw a", "kw b"])
        assert out["counters"]["posts_raw"] == 4
        assert out["counters"]["posts_unique"] == 3


# ── the empty-array trap ──────────────────────────────────────────


class TestEmptyArrayTrap:
    def test_empty_first_page_is_flagged(self):
        session = MagicMock()
        session.get.return_value = _response([])
        result = _crawler(session).search_keyword("sensitive term")
        assert result["empty"] is True
        assert result["posts"] == []

    def test_empty_later_page_is_not_flagged(self):
        """Running out of results after page 1 is ordinary pagination end."""
        session = MagicMock()
        session.get.side_effect = [
            _response([_post("1")], after="cur1"),
            _response([]),
        ]
        result = _crawler(session).search_keyword("data engineer")
        assert result["empty"] is False
        assert len(result["posts"]) == 1

    def test_counter_and_list_surface_to_the_flow(self):
        session = MagicMock()
        session.get.side_effect = [
            _response([]),                # kw a: silently empty
            _response([_post("1")]),      # kw b: fine
            _response([]),                # kw c: silently empty
        ]
        out = _crawler(session).search_all(["kw a", "kw b", "kw c"])
        assert out["counters"]["empty_keywords"] == 2
        assert out["empty_keyword_list"] == ["kw a", "kw c"]

    def test_empty_is_logged_at_error_level(self, caplog):
        session = MagicMock()
        session.get.return_value = _response([])
        with caplog.at_level("ERROR", logger="src.crawlers.threads.search"):
            _crawler(session).search_keyword("sensitive term")
        assert any(r.levelname == "ERROR" for r in caplog.records)
        assert "EMPTY RESULT" in caplog.text

    def test_all_empty_run_is_not_reported_as_success(self):
        """An all-empty run must be distinguishable from 'no jobs today'."""
        session = MagicMock()
        session.get.return_value = _response([])
        out = _crawler(session).search_all(["a", "b"])
        assert out["counters"]["posts_unique"] == 0
        assert out["counters"]["empty_keywords"] == 2


# ── rate-limit budget ─────────────────────────────────────────────


class TestQueryBudget:
    def test_default_matches_meta_documented_limit(self):
        assert QueryBudget().limit == DAILY_QUERY_BUDGET == 2200

    def test_spend_decrements_remaining(self):
        b = QueryBudget(limit=3)
        b.spend()
        assert (b.used, b.remaining) == (1, 2)

    def test_raises_when_exhausted(self):
        b = QueryBudget(limit=1)
        b.spend()
        with pytest.raises(ThreadsQuotaExceeded):
            b.spend()

    def test_empty_results_are_refunded(self):
        """Meta does not count queries that return no results."""
        session = MagicMock()
        session.get.return_value = _response([])
        budget = QueryBudget(limit=10)
        _crawler(session, budget=budget).search_keyword("sensitive")
        assert budget.used == 0

    def test_non_empty_results_are_charged(self):
        session = MagicMock()
        session.get.side_effect = [
            _response([_post("1")], after="c1"),
            _response([_post("2")]),
        ]
        budget = QueryBudget(limit=10)
        _crawler(session, budget=budget).search_keyword("data engineer")
        assert budget.used == 2

    def test_search_all_stops_at_the_cap_instead_of_overrunning(self):
        session = MagicMock()
        session.get.return_value = _response([_post("1")])
        budget = QueryBudget(limit=2)
        out = _crawler(session, budget=budget).search_all(["a", "b", "c", "d"])
        assert out["counters"]["budget_exhausted"] == 1
        assert budget.used == 2
        assert session.get.call_count == 2

    def test_queries_used_is_reported(self):
        session = MagicMock()
        session.get.return_value = _response([_post("1")])
        out = _crawler(session, budget=QueryBudget(limit=10)).search_all(["a", "b"])
        assert out["counters"]["queries_used"] == 2


# ── request shape & failures ──────────────────────────────────────


class TestRequestShape:
    def test_sends_token_and_documented_fields(self):
        session = MagicMock()
        session.get.return_value = _response([_post("1")])
        _crawler(session).search_keyword("data engineer")
        params = session.get.call_args.kwargs["params"]
        assert params["access_token"] == "fake-token"
        assert params["q"] == "data engineer"
        for field in ("id", "text", "permalink", "timestamp", "username"):
            assert field in params["fields"]

    def test_owner_field_is_never_requested(self):
        """Meta explicitly excludes `owner`; asking for it fails the whole call."""
        session = MagicMock()
        session.get.return_value = _response([_post("1")])
        _crawler(session).search_keyword("data engineer")
        assert "owner" not in session.get.call_args.kwargs["params"]["fields"]


class TestFailures:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failure_aborts_instead_of_burning_budget(self, status):
        session = MagicMock()
        session.get.return_value = _response([], status=status)
        with pytest.raises(ThreadsAuthError):
            _crawler(session).search_all(["a", "b", "c"])
        assert session.get.call_count == 1

    def test_network_error_is_counted_not_swallowed(self):
        session = MagicMock()
        session.get.side_effect = [
            requests.RequestException("boom"),
            _response([_post("1")]),
        ]
        out = _crawler(session).search_all(["a", "b"])
        assert out["counters"]["failed_keywords"] == 1
        assert out["counters"]["posts_unique"] == 1

    def test_network_error_is_not_mistaken_for_an_empty_keyword(self):
        session = MagicMock()
        session.get.side_effect = requests.RequestException("boom")
        out = _crawler(session).search_all(["a"])
        assert out["counters"]["empty_keywords"] == 0
        assert out["counters"]["failed_keywords"] == 1

    def test_kill_switch_stops_the_keyword_loop(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_KILL_SWITCH", "1")
        session = MagicMock()
        session.get.return_value = _response([_post("1")])
        out = _crawler(session).search_all(["a", "b"])
        assert session.get.call_count == 0
        assert out["counters"]["keywords"] == 0
