import gzip
from unittest.mock import MagicMock

import pytest

from src.crawlers.vietnamworks.detail.detail_crawler import DetailCrawler
from src.crawlers.vietnamworks.detail.fetcher import BlockedError, ExpiredError, TransientError


URL = "https://www.vietnamworks.com/data-engineer-1-jv"


def make_crawler(claim_jobs, fetch_side_effect):
    log = MagicMock()
    log.claim_next.side_effect = list(claim_jobs) + [None]
    fetcher = MagicMock()
    fetcher.fetch.side_effect = fetch_side_effect
    minio = MagicMock()
    rate = MagicMock()
    breaker = MagicMock()
    breaker.is_open.return_value = False
    crawler = DetailCrawler(
        log=log, fetcher=fetcher, minio=minio, run_id="testrun",
        rate_limiter=rate, breaker=breaker,
    )
    return crawler, log, fetcher, minio


def test_run_success_uploads_gzip_and_marks_success(monkeypatch):
    monkeypatch.setattr("src.crawlers.vietnamworks.detail.detail_crawler.jitter_sleep", lambda *a, **k: None)
    monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    crawler, log, fetcher, minio = make_crawler(
        claim_jobs=[("12345", URL)],
        fetch_side_effect=[(200, "<html>real body</html>", 100)],
    )

    counters = crawler.run()
    assert counters["success"] == 1

    minio.upload_bytes.assert_called_once()
    args, kwargs = minio.upload_bytes.call_args
    bucket, key, payload, ctype = args
    assert key == "details/vietnamworks/html/testrun/12345.html.gz"
    assert ctype == "application/gzip"
    assert gzip.decompress(payload).decode() == "<html>real body</html>"

    log.mark_success.assert_called_once_with("12345", 200,
                                             "details/vietnamworks/html/testrun/12345.html.gz",
                                             "testrun")


def test_run_expired_marks_expired(monkeypatch):
    monkeypatch.setattr("src.crawlers.vietnamworks.detail.detail_crawler.jitter_sleep", lambda *a, **k: None)
    monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    crawler, log, _, minio = make_crawler(
        claim_jobs=[("12345", URL)],
        fetch_side_effect=[ExpiredError("404")],
    )
    counters = crawler.run()
    assert counters["expired"] == 1
    log.mark_expired.assert_called_once_with("12345")
    minio.upload_bytes.assert_not_called()


def test_run_transient_marks_failed(monkeypatch):
    monkeypatch.setattr("src.crawlers.vietnamworks.detail.detail_crawler.jitter_sleep", lambda *a, **k: None)
    monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    crawler, log, _, minio = make_crawler(
        claim_jobs=[("12345", URL)],
        fetch_side_effect=[TransientError("500", 500)],
    )
    counters = crawler.run()
    assert counters["failed"] == 1
    log.mark_failed.assert_called_once()
    minio.upload_bytes.assert_not_called()


def test_run_blocked_triggers_kill_and_stops(monkeypatch):
    monkeypatch.setattr("src.crawlers.vietnamworks.detail.detail_crawler.jitter_sleep", lambda *a, **k: None)
    monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    crawler, log, _, _ = make_crawler(
        claim_jobs=[("12345", URL), ("67890", URL)],
        fetch_side_effect=[BlockedError("403"), (200, "x", 1)],
    )
    counters = crawler.run()
    # Stops after first BlockedError, second job not processed
    assert counters["failed"] == 1
    assert counters.get("success", 0) == 0
    import os
    assert os.environ.get("CRAWLER_KILL_SWITCH") == "1"
    monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)


def test_run_rejects_disallowed_url(monkeypatch):
    monkeypatch.setattr("src.crawlers.vietnamworks.detail.detail_crawler.jitter_sleep", lambda *a, **k: None)
    monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    crawler, log, fetcher, _ = make_crawler(
        claim_jobs=[("1", "https://evil.com/x")],
        fetch_side_effect=[],
    )
    counters = crawler.run()
    assert counters["failed"] == 1
    fetcher.fetch.assert_not_called()
    log.mark_failed.assert_called_once()


def test_kill_switch_aborts_immediately(monkeypatch):
    monkeypatch.setenv("CRAWLER_KILL_SWITCH", "1")
    crawler, log, fetcher, _ = make_crawler(
        claim_jobs=[("1", URL)],
        fetch_side_effect=[],
    )
    counters = crawler.run()
    assert counters == {"success": 0, "failed": 0, "expired": 0, "skipped": 0}
    fetcher.fetch.assert_not_called()
    log.claim_next.assert_not_called()
    monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
