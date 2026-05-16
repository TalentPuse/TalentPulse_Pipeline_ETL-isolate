"""Tests for LinkedIn detail crawler — process_one and run loop."""
import gzip
from unittest.mock import MagicMock, patch

import pytest

from src.crawlers.linkedin.detail import LinkedInDetailCrawler, SOURCE
from src.crawlers.linkedin.fetcher import BlockedError, TransientError


JOB_ID = "12345"
URL = f"https://www.linkedin.com/jobs/view/{JOB_ID}"
LONG_HTML = "<html>" + "x" * 600 + "</html>"  # >500 chars


def make_crawler(claim_jobs, fetch_side_effect):
    log = MagicMock()
    log.claim_next.side_effect = list(claim_jobs) + [None]
    fetcher = MagicMock()
    fetcher.fetch.side_effect = fetch_side_effect
    minio = MagicMock()
    rate_limiter = MagicMock()
    breaker = MagicMock()
    breaker.is_open.return_value = False
    breaker.reason.return_value = None
    crawler = LinkedInDetailCrawler(
        log=log, fetcher=fetcher, minio=minio, run_id="testrun",
        rate_limiter=rate_limiter, breaker=breaker,
    )
    return crawler, log, fetcher, minio


# ── process_one ──────────────────────────────────────────────────


class TestProcessOne:
    def test_success_uploads_gzip_and_marks_success(self):
        log = MagicMock()
        fetcher = MagicMock()
        fetcher.fetch.return_value = (200, LONG_HTML, 100)
        minio = MagicMock()
        rate_limiter = MagicMock()
        breaker = MagicMock()

        crawler = LinkedInDetailCrawler(
            log=log, fetcher=fetcher, minio=minio, run_id="testrun",
            rate_limiter=rate_limiter, breaker=breaker,
        )
        result = crawler.process_one(JOB_ID, URL)

        assert result == "success"
        minio.upload_bytes.assert_called_once()
        args = minio.upload_bytes.call_args[0]
        bucket, key, payload, ctype = args
        assert key == "details/linkedin/html/testrun/12345.html.gz"
        assert ctype == "application/gzip"
        assert gzip.decompress(payload).decode() == LONG_HTML
        log.mark_success.assert_called_once()

    def test_short_html_marks_failed(self):
        log = MagicMock()
        fetcher = MagicMock()
        fetcher.fetch.return_value = (200, "<html>short</html>", 50)
        minio = MagicMock()
        breaker = MagicMock()

        crawler = LinkedInDetailCrawler(
            log=log, fetcher=fetcher, minio=minio, run_id="testrun",
            breaker=breaker,
        )
        result = crawler.process_one(JOB_ID, URL)

        assert result == "failed"
        breaker.record.assert_called_with(None)
        log.mark_failed.assert_called_once()
        minio.upload_bytes.assert_not_called()

    def test_empty_html_marks_failed(self):
        log = MagicMock()
        fetcher = MagicMock()
        fetcher.fetch.return_value = (200, "", 10)
        minio = MagicMock()
        breaker = MagicMock()

        crawler = LinkedInDetailCrawler(
            log=log, fetcher=fetcher, minio=minio, run_id="testrun",
            breaker=breaker,
        )
        result = crawler.process_one(JOB_ID, URL)

        assert result == "failed"

    def test_blocked_raises_and_triggers_safety(self):
        log = MagicMock()
        fetcher = MagicMock()
        fetcher.fetch.side_effect = BlockedError("403 Forbidden")
        minio = MagicMock()
        breaker = MagicMock()

        crawler = LinkedInDetailCrawler(
            log=log, fetcher=fetcher, minio=minio, run_id="testrun",
            breaker=breaker,
        )
        with pytest.raises(BlockedError):
            crawler.process_one(JOB_ID, URL)

        breaker.record.assert_called_with(403)
        log.mark_failed.assert_called_once_with(JOB_ID, 403, "403 Forbidden", source=SOURCE)

    def test_transient_returns_failed(self):
        log = MagicMock()
        fetcher = MagicMock()
        fetcher.fetch.side_effect = TransientError("500 error", 500)
        minio = MagicMock()
        breaker = MagicMock()

        crawler = LinkedInDetailCrawler(
            log=log, fetcher=fetcher, minio=minio, run_id="testrun",
            breaker=breaker,
        )
        result = crawler.process_one(JOB_ID, URL)

        assert result == "failed"
        breaker.record.assert_called_with(500)
        log.mark_failed.assert_called_once()


# ── run loop ─────────────────────────────────────────────────────


class TestRun:
    def test_run_success_processes_jobs(self, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.detail.jitter_sleep", lambda *a, **k: None)
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

        crawler, log, fetcher, minio = make_crawler(
            claim_jobs=[(JOB_ID, URL)],
            fetch_side_effect=[(200, LONG_HTML, 100)],
        )
        counters = crawler.run()
        assert counters["success"] == 1

    def test_run_transient_counts_failure(self, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.detail.jitter_sleep", lambda *a, **k: None)
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

        crawler, log, fetcher, minio = make_crawler(
            claim_jobs=[(JOB_ID, URL)],
            fetch_side_effect=[TransientError("500", 500)],
        )
        counters = crawler.run()
        assert counters["failed"] == 1

    def test_run_blocked_stops_loop(self, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.detail.jitter_sleep", lambda *a, **k: None)
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

        crawler, log, fetcher, _ = make_crawler(
            claim_jobs=[(JOB_ID, URL), ("67890", URL)],
            fetch_side_effect=[BlockedError("403"), (200, LONG_HTML, 1)],
        )
        counters = crawler.run()
        assert counters["failed"] == 1
        assert counters.get("success", 0) == 0
        import os
        assert os.environ.get("CRAWLER_KILL_SWITCH") == "1"
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    def test_run_respects_max_jobs(self, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.detail.jitter_sleep", lambda *a, **k: None)
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

        crawler, log, _, _ = make_crawler(
            claim_jobs=[("1", URL), ("2", URL), ("3", URL)],
            fetch_side_effect=[
                (200, LONG_HTML, 100),
                (200, LONG_HTML, 100),
                (200, LONG_HTML, 100),
            ],
        )
        counters = crawler.run(max_jobs=2)
        assert counters["success"] == 2

    def test_run_no_pending_jobs(self, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.detail.jitter_sleep", lambda *a, **k: None)
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

        log = MagicMock()
        log.claim_next.return_value = None
        crawler = LinkedInDetailCrawler(
            log=log, fetcher=MagicMock(), minio=MagicMock(), run_id="testrun",
        )
        counters = crawler.run()
        assert counters == {"success": 0, "failed": 0, "skipped": 0}

    def test_run_breaker_open_stops_loop(self, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.detail.jitter_sleep", lambda *a, **k: None)
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

        log = MagicMock()
        log.claim_next.return_value = None
        breaker = MagicMock()
        breaker.is_open.return_value = True
        breaker.reason.return_value = "too many errors"
        crawler = LinkedInDetailCrawler(
            log=log, fetcher=MagicMock(), minio=MagicMock(), run_id="testrun",
            breaker=breaker,
        )
        counters = crawler.run()
        assert counters == {"success": 0, "failed": 0, "skipped": 0}


# ── Kill switch ──────────────────────────────────────────────────


class TestKillSwitch:
    def test_kill_switch_active_before_start(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_KILL_SWITCH", "1")
        log = MagicMock()
        fetcher = MagicMock()
        crawler = LinkedInDetailCrawler(
            log=log, fetcher=fetcher, minio=MagicMock(), run_id="testrun",
        )
        counters = crawler.run()
        assert counters == {"success": 0, "failed": 0, "skipped": 0}
        fetcher.fetch.assert_not_called()
        log.claim_next.assert_not_called()
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    def test_kill_switch_triggered_mid_run(self, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.detail.jitter_sleep", lambda *a, **k: None)
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

        crawler, log, fetcher, _ = make_crawler(
            claim_jobs=[(JOB_ID, URL)],
            fetch_side_effect=[BlockedError("403")],
        )
        crawler.run()
        import os
        assert os.environ.get("CRAWLER_KILL_SWITCH") == "1"
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
