import gzip
from unittest.mock import MagicMock

from src.crawlers.topcv.detail import TopCVDetailCrawler

URL = "https://www.topcv.vn/viec-lam/data-engineer/2114998.html"


def make_crawler(claim_jobs, fetch_side_effect):
    browser = MagicMock()
    browser.fetch_page.side_effect = fetch_side_effect
    log = MagicMock()
    log.claim_next.side_effect = list(claim_jobs) + [None]
    minio = MagicMock()
    breaker = MagicMock()
    breaker.is_open.return_value = False
    crawler = TopCVDetailCrawler(
        browser=browser, log=log, minio=minio, run_id="testrun", breaker=breaker,
    )
    return crawler, log, minio


def test_run_success_uploads_gzip_and_marks_success(monkeypatch):
    monkeypatch.setattr("src.crawlers.topcv.detail.jitter_sleep", lambda *a, **k: None)
    monkeypatch.setattr("src.crawlers.topcv.detail.safety.is_killed", lambda: False)

    body = "<html>" + "x" * 2000 + "</html>"
    crawler, log, minio = make_crawler(
        claim_jobs=[("2114998", URL)],
        fetch_side_effect=[body],
    )

    counters = crawler.run()
    assert counters["success"] == 1

    minio.upload_bytes.assert_called_once()
    bucket, key, payload, ctype = minio.upload_bytes.call_args.args
    assert key == "details/topcv/html/testrun/2114998.html.gz"
    assert ctype == "application/gzip"
    assert gzip.decompress(payload).decode() == body
    log.mark_success.assert_called_once_with(
        "2114998", 200, "details/topcv/html/testrun/2114998.html.gz", "testrun", source="topcv"
    )


def test_run_tiny_response_marks_failed(monkeypatch):
    monkeypatch.setattr("src.crawlers.topcv.detail.jitter_sleep", lambda *a, **k: None)
    monkeypatch.setattr("src.crawlers.topcv.detail.safety.is_killed", lambda: False)

    crawler, log, minio = make_crawler(
        claim_jobs=[("2114998", URL)],
        fetch_side_effect=["<html>tiny</html>"],
    )
    counters = crawler.run()
    assert counters["failed"] == 1
    minio.upload_bytes.assert_not_called()
    log.mark_failed.assert_called_once()
