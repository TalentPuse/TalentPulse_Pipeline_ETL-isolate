"""Tests for LinkedIn Fetcher — retry, block detection, backoff."""
import pytest
import requests

from src.crawlers.linkedin.fetcher import BlockedError, Fetcher, TransientError


URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/12345"


def make_fetcher():
    sleeps = []
    f = Fetcher(session=requests.Session(), sleeper=lambda d: sleeps.append(d))
    return f, sleeps


# ── 200 success ───────────────────────────────────────────────────


class TestSuccess:
    def test_fetch_200_returns_body(self, requests_mock):
        requests_mock.get(URL, text="<html>job detail</html>", status_code=200)
        f, _ = make_fetcher()
        status, html, latency = f.fetch(URL)
        assert status == 200
        assert "job detail" in html
        assert latency >= 0

    def test_fetch_200_no_block_keywords(self, requests_mock):
        requests_mock.get(URL, text="<html>normal page content</html>", status_code=200)
        f, _ = make_fetcher()
        status, html, _ = f.fetch(URL)
        assert status == 200


# ── Block detection ───────────────────────────────────────────────


class TestBlockDetection:
    @pytest.mark.parametrize("keyword", [
        "authwall", "sign in", "join now", "captcha",
        "access denied", "attention required",
    ])
    def test_block_keyword_in_body_raises(self, requests_mock, keyword):
        requests_mock.get(URL, text=f"<html>{keyword} page</html>", status_code=200)
        f, _ = make_fetcher()
        with pytest.raises(BlockedError, match="block keyword"):
            f.fetch(URL)

    def test_block_keyword_case_insensitive(self, requests_mock):
        requests_mock.get(URL, text="<html>Sign In to continue</html>", status_code=200)
        f, _ = make_fetcher()
        with pytest.raises(BlockedError):
            f.fetch(URL)

    def test_block_keyword_only_in_first_5000_chars(self, requests_mock):
        padding = "x" * 5000
        requests_mock.get(URL, text=f"<html>{padding}authwall</html>", status_code=200)
        f, _ = make_fetcher()
        status, _, _ = f.fetch(URL)
        assert status == 200

    def test_403_raises_blocked(self, requests_mock):
        requests_mock.get(URL, status_code=403)
        f, _ = make_fetcher()
        with pytest.raises(BlockedError, match="403"):
            f.fetch(URL)


# ── 404 Not Found ────────────────────────────────────────────────


class TestNotFound:
    def test_404_raises_transient(self, requests_mock):
        requests_mock.get(URL, status_code=404)
        f, _ = make_fetcher()
        with pytest.raises(TransientError) as exc:
            f.fetch(URL)
        assert exc.value.status == 404


# ── Retry + backoff ──────────────────────────────────────────────


class TestRetry:
    def test_429_retries_then_succeeds(self, requests_mock):
        requests_mock.get(URL, [
            {"status_code": 429, "headers": {"Retry-After": "1"}},
            {"status_code": 200, "text": "<html>after retry</html>"},
        ])
        f, sleeps = make_fetcher()
        status, html, _ = f.fetch(URL)
        assert status == 200
        assert "after retry" in html
        assert len(sleeps) == 1
        assert sleeps[0] >= 1

    def test_429_respects_retry_after_header(self, requests_mock):
        requests_mock.get(URL, [
            {"status_code": 429, "headers": {"Retry-After": "200"}},
            {"status_code": 200, "text": "<html>ok</html>"},
        ])
        f, sleeps = make_fetcher()
        f.fetch(URL)
        assert sleeps[0] >= 200

    def test_429_exhausted_raises_transient(self, requests_mock):
        requests_mock.get(URL, status_code=429)
        f, sleeps = make_fetcher()
        with pytest.raises(TransientError) as exc:
            f.fetch(URL)
        assert exc.value.status == 429
        assert len(sleeps) == 3

    def test_503_retries(self, requests_mock):
        requests_mock.get(URL, [
            {"status_code": 503},
            {"status_code": 200, "text": "<html>ok</html>"},
        ])
        f, sleeps = make_fetcher()
        status, _, _ = f.fetch(URL)
        assert status == 200
        assert len(sleeps) == 1

    def test_500_retry_exhausted(self, requests_mock):
        requests_mock.get(URL, status_code=500)
        f, _ = make_fetcher()
        with pytest.raises(TransientError) as exc:
            f.fetch(URL)
        assert exc.value.status == 500

    def test_999_retry_exhausted(self, requests_mock):
        requests_mock.get(URL, status_code=999)
        f, _ = make_fetcher()
        with pytest.raises(TransientError) as exc:
            f.fetch(URL)
        assert exc.value.status == 999


# ── Network errors ───────────────────────────────────────────────


class TestNetworkErrors:
    def test_timeout_retries(self, requests_mock):
        requests_mock.get(URL, [
            {"exc": requests.Timeout("timeout")},
            {"status_code": 200, "text": "<html>ok</html>"},
        ])
        f, sleeps = make_fetcher()
        status, _, _ = f.fetch(URL)
        assert status == 200
        assert len(sleeps) == 1

    def test_timeout_exhausted_raises_transient(self, requests_mock):
        requests_mock.get(URL, exc=requests.Timeout("timeout"))
        f, _ = make_fetcher()
        with pytest.raises(TransientError) as exc:
            f.fetch(URL)
        assert exc.value.status is None

    def test_connection_error_retries(self, requests_mock):
        requests_mock.get(URL, [
            {"exc": requests.ConnectionError("refused")},
            {"status_code": 200, "text": "<html>ok</html>"},
        ])
        f, _ = make_fetcher()
        status, _, _ = f.fetch(URL)
        assert status == 200


# ── Unexpected status codes ──────────────────────────────────────


class TestUnexpectedStatus:
    def test_418_raises_transient(self, requests_mock):
        requests_mock.get(URL, status_code=418)
        f, _ = make_fetcher()
        with pytest.raises(TransientError):
            f.fetch(URL)

    def test_301_no_redirect_loop(self, requests_mock):
        """allow_redirects=True by default, but if final status isn't handled, raises."""
        requests_mock.get(URL, status_code=302)
        f, _ = make_fetcher()
        with pytest.raises(TransientError):
            f.fetch(URL)
