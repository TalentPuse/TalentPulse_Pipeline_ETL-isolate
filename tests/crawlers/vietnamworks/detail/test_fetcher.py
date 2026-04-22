import pytest
import requests

from src.crawlers.vietnamworks.detail.fetcher import (
    BlockedError,
    ExpiredError,
    Fetcher,
    TransientError,
)

URL = "https://www.vietnamworks.com/some-job-1-jv"


def make_fetcher():
    sleeps = []
    f = Fetcher(session=requests.Session(), sleeper=lambda d: sleeps.append(d))
    return f, sleeps


def test_fetch_200_returns_body(requests_mock):
    requests_mock.get(URL, text="<html>ok</html>", status_code=200)
    f, _ = make_fetcher()
    status, html, latency = f.fetch(URL)
    assert status == 200
    assert "ok" in html
    assert latency >= 0


def test_fetch_403_raises_blocked(requests_mock):
    requests_mock.get(URL, status_code=403)
    f, _ = make_fetcher()
    with pytest.raises(BlockedError):
        f.fetch(URL)


def test_fetch_404_raises_expired(requests_mock):
    requests_mock.get(URL, status_code=404)
    f, _ = make_fetcher()
    with pytest.raises(ExpiredError):
        f.fetch(URL)


def test_fetch_429_retries_then_succeeds(requests_mock):
    requests_mock.get(
        URL,
        [
            {"status_code": 429, "headers": {"Retry-After": "1"}},
            {"status_code": 200, "text": "<html>after retry</html>"},
        ],
    )
    f, sleeps = make_fetcher()
    status, html, _ = f.fetch(URL)
    assert status == 200
    assert "after retry" in html
    assert len(sleeps) == 1
    assert sleeps[0] >= 1


def test_fetch_500_retry_exhausted(requests_mock):
    requests_mock.get(URL, status_code=500)
    f, _ = make_fetcher()
    with pytest.raises(TransientError) as exc:
        f.fetch(URL)
    assert exc.value.status == 500


def test_fetch_block_keyword_in_body_raises(requests_mock):
    requests_mock.get(URL, text="<html>Cloudflare attention required</html>", status_code=200)
    f, _ = make_fetcher()
    with pytest.raises(BlockedError):
        f.fetch(URL)


def test_fetch_unexpected_4xx_raises_transient(requests_mock):
    requests_mock.get(URL, status_code=418)
    f, _ = make_fetcher()
    with pytest.raises(TransientError):
        f.fetch(URL)
