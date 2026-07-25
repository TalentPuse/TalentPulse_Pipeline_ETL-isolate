"""Tests for ITviec detail page crawler."""
import gzip
import os
from unittest.mock import MagicMock, patch

import pytest

from src.crawlers.itviec.detail import ITviecDetailCrawler, SOURCE
from tests.parsers.itviec._fixtures import make_detail_html, SAMPLE_JOB_POSTING


DETAIL_URL = "https://itviec.com/it-jobs/senior-data-engineer-acme-corp-4611"


def _make_crawler(claim_jobs=None, browser_pages=None, breaker_open=False):
    """Build crawler with mocked dependencies. Returns (crawler, log, browser, minio)."""
    browser = MagicMock()
    if browser_pages is not None:
        browser.fetch_page.side_effect = browser_pages
    else:
        browser.fetch_page.return_value = make_detail_html()

    log = MagicMock()
    if claim_jobs is not None:
        log.claim_next.side_effect = list(claim_jobs) + [None]
    else:
        log.claim_next.return_value = None

    minio = MagicMock()

    breaker = MagicMock()
    breaker.is_open.return_value = breaker_open

    crawler = ITviecDetailCrawler(
        browser=browser, log=log, minio=minio,
        run_id="testrun", breaker=breaker,
    )
    return crawler, log, browser, minio


class TestProcessOne:
    def test_success_uploads_gzipped_html(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, minio = _make_crawler()
        html = make_detail_html()
        browser.fetch_page.return_value = html

        result = crawler.process_one("4611", DETAIL_URL)

        assert result == "success"
        minio.upload_bytes.assert_called_once()
        args = minio.upload_bytes.call_args[0]
        bucket, key, payload, ctype = args
        assert key == "details/itviec/html/testrun/4611.html.gz"
        assert ctype == "application/gzip"
        assert gzip.decompress(payload).decode("utf-8") == html

    def test_success_marks_crawl_log(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, _, _ = _make_crawler()

        crawler.process_one("4611", DETAIL_URL)

        log.mark_success.assert_called_once_with(
            "4611", 200,
            "details/itviec/html/testrun/4611.html.gz",
            "testrun", source=SOURCE,
        )

    def test_fetch_exception_marks_failed(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, minio = _make_crawler()
        browser.fetch_page.side_effect = RuntimeError("timeout")

        result = crawler.process_one("4611", DETAIL_URL)

        assert result == "failed"
        log.mark_failed.assert_called_once()
        minio.upload_bytes.assert_not_called()

    def test_empty_response_marks_failed(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, minio = _make_crawler()
        browser.fetch_page.return_value = "<html></html>"  # < 1000 chars

        result = crawler.process_one("4611", DETAIL_URL)

        assert result == "failed"
        log.mark_failed.assert_called_once()

    def test_cloudflare_block_triggers_safety(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, _ = _make_crawler()
        blocked_html = "<html><head><title>Attention Required</title></head>" + "x" * 2000
        browser.fetch_page.return_value = blocked_html

        result = crawler.process_one("4611", DETAIL_URL)

        assert result == "failed"
        log.mark_failed.assert_called_once()
        assert os.environ.get("CRAWLER_KILL_SWITCH") == "1"
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    def test_access_denied_triggers_safety(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, _ = _make_crawler()
        blocked_html = "<html><head><title>Access Denied</title></head>" + "x" * 2000
        browser.fetch_page.return_value = blocked_html

        result = crawler.process_one("4611", DETAIL_URL)

        assert result == "failed"
        assert os.environ.get("CRAWLER_KILL_SWITCH") == "1"
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)


class TestRun:
    @patch("src.crawlers.itviec.detail.jitter_sleep", lambda *a, **k: None)
    def test_processes_pending_jobs(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, minio = _make_crawler(
            claim_jobs=[("4611", DETAIL_URL), ("4612", DETAIL_URL)],
        )

        counters = crawler.run()

        assert counters["success"] == 2
        assert log.mark_success.call_count == 2

    @patch("src.crawlers.itviec.detail.jitter_sleep", lambda *a, **k: None)
    def test_stops_at_max_jobs(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, _ = _make_crawler(
            claim_jobs=[("4611", DETAIL_URL), ("4612", DETAIL_URL)],
        )

        counters = crawler.run(max_jobs=1)

        assert counters["success"] == 1
        assert log.mark_success.call_count == 1

    @patch("src.crawlers.itviec.detail.jitter_sleep", lambda *a, **k: None)
    def test_stops_when_queue_empty(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, _, _ = _make_crawler(claim_jobs=[])

        counters = crawler.run()

        assert counters == {"success": 0, "failed": 0, "skipped": 0}

    def test_kill_switch_aborts_before_start(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_KILL_SWITCH", "1")
        crawler, log, browser, _ = _make_crawler(
            claim_jobs=[("4611", DETAIL_URL)],
        )

        counters = crawler.run()

        assert counters == {"success": 0, "failed": 0, "skipped": 0}
        log.claim_next.assert_not_called()
        browser.fetch_page.assert_not_called()
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)

    @patch("src.crawlers.itviec.detail.jitter_sleep", lambda *a, **k: None)
    def test_stops_when_breaker_opens(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, _ = _make_crawler(
            claim_jobs=[("4611", DETAIL_URL)],
            breaker_open=True,
        )

        counters = crawler.run()

        assert counters == {"success": 0, "failed": 0, "skipped": 0}
        browser.fetch_page.assert_not_called()

    @patch("src.crawlers.itviec.detail.jitter_sleep", lambda *a, **k: None)
    def test_process_one_crash_marks_failed_not_left_in_progress(self, monkeypatch):
        """A crash inside process_one (e.g. minio.upload_bytes raising) must
        still resolve the claimed row via mark_failed, never leave it stuck
        in_progress for the next run to be unable to reclaim."""
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        crawler, log, browser, minio = _make_crawler(
            claim_jobs=[("4611", DETAIL_URL)],
        )
        minio.upload_bytes.side_effect = RuntimeError("minio connection reset")

        counters = crawler.run()

        assert counters["failed"] == 1
        assert counters["success"] == 0
        log.mark_failed.assert_called_once()
        call_args = log.mark_failed.call_args
        assert call_args[0][0] == "4611"
        assert call_args[1]["source"] == SOURCE
        # crawler must not have crashed out of run() — loop kept going and
        # exhausted the queue normally.
        log.claim_next.assert_called()

    @patch("src.crawlers.itviec.detail.jitter_sleep", lambda *a, **k: None)
    def test_mixed_success_and_failure(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_KILL_SWITCH", raising=False)
        good_html = make_detail_html()
        browser = MagicMock()
        browser.fetch_page.side_effect = [good_html, RuntimeError("timeout")]

        log = MagicMock()
        log.claim_next.side_effect = [("4611", DETAIL_URL), ("4612", DETAIL_URL), None]
        minio = MagicMock()
        breaker = MagicMock()
        breaker.is_open.return_value = False

        crawler = ITviecDetailCrawler(
            browser=browser, log=log, minio=minio,
            run_id="testrun", breaker=breaker,
        )

        counters = crawler.run()

        assert counters["success"] == 1
        assert counters["failed"] == 1
