"""Tests for ITviec seeder — URL parsing + queue insertion."""
from unittest.mock import MagicMock

import pytest

from src.queue.itviec_seeder import extract_job_id, seed_from_urls, ITVIEC_URL_RE


# ── extract_job_id ───────────────────────────────────────────────────


class TestExtractJobId:
    def test_standard_url(self):
        url = "https://itviec.com/it-jobs/senior-data-engineer-acme-corp-4611"
        assert extract_job_id(url) == "4611"

    def test_single_word_slug(self):
        url = "https://itviec.com/it-jobs/python-123"
        assert extract_job_id(url) == "123"

    def test_long_slug(self):
        url = "https://itviec.com/it-jobs/ai-engineer-python-ml-deep-learning-bigcorp-ho-chi-minh-2549"
        assert extract_job_id(url) == "2549"

    def test_rejects_non_itviec_domain(self):
        url = "https://topdev.vn/it-jobs/data-engineer-acme-4611"
        assert extract_job_id(url) is None

    def test_rejects_http_scheme(self):
        url = "http://itviec.com/it-jobs/data-engineer-acme-4611"
        assert extract_job_id(url) is None

    def test_rejects_missing_numeric_id(self):
        url = "https://itviec.com/it-jobs/data-engineer-acme"
        assert extract_job_id(url) is None

    def test_rejects_trailing_slash(self):
        url = "https://itviec.com/it-jobs/data-engineer-acme-4611/"
        assert extract_job_id(url) is None

    def test_rejects_query_params(self):
        url = "https://itviec.com/it-jobs/data-engineer-acme-4611?ref=search"
        assert extract_job_id(url) is None

    def test_rejects_non_it_jobs_path(self):
        url = "https://itviec.com/companies/acme-4611"
        assert extract_job_id(url) is None

    def test_rejects_empty_string(self):
        assert extract_job_id("") is None

    def test_rejects_apply_url(self):
        url = "https://itviec.com/job/senior-data-engineer-acme-corp-4611/job_applications/new"
        assert extract_job_id(url) is None


# ── ITVIEC_URL_RE ────────────────────────────────────────────────────


class TestItviecUrlRegex:
    def test_matches_valid_url(self):
        assert ITVIEC_URL_RE.match("https://itviec.com/it-jobs/my-job-99") is not None

    def test_captures_numeric_id(self):
        m = ITVIEC_URL_RE.match("https://itviec.com/it-jobs/slug-here-42")
        assert m.group(1) == "42"

    def test_no_match_without_id(self):
        assert ITVIEC_URL_RE.match("https://itviec.com/it-jobs/no-id-here") is None


# ── seed_from_urls ───────────────────────────────────────────────────


class TestSeedFromUrls:
    # The seeder now collects valid (job_id, url) pairs and enqueues them in a
    # single CrawlLog.enqueue_many() call, so `enqueued` reflects that batch's
    # return value. URL-level rejects (bad regex) are still counted per row.
    def test_enqueues_valid_urls(self):
        log = MagicMock()
        log.enqueue_many.return_value = 2

        urls = [
            "https://itviec.com/it-jobs/data-engineer-acme-4611",
            "https://itviec.com/it-jobs/ai-engineer-bigcorp-2549",
        ]
        result = seed_from_urls(urls, log=log)

        assert result["enqueued"] == 2
        assert result["rejected_url"] == 0
        log.enqueue_many.assert_called_once_with(
            [("4611", urls[0]), ("2549", urls[1])], source="itviec"
        )

    def test_counts_rejected_urls(self):
        log = MagicMock()
        log.enqueue_many.return_value = 1
        urls = [
            "https://itviec.com/it-jobs/valid-job-111",
            "https://evil.com/fake-4611",
            "not-even-a-url",
        ]
        result = seed_from_urls(urls, log=log)

        assert result["enqueued"] == 1
        assert result["rejected_url"] == 2
        log.enqueue_many.assert_called_once_with([("111", urls[0])], source="itviec")

    def test_empty_list(self):
        log = MagicMock()
        log.enqueue_many.return_value = 0
        result = seed_from_urls([], log=log)

        assert result["enqueued"] == 0
        assert result["rejected_url"] == 0
        log.enqueue_many.assert_called_once_with([], source="itviec")

    def test_all_invalid(self):
        log = MagicMock()
        log.enqueue_many.return_value = 0
        urls = ["bad", "worse", "https://other.com/123"]
        result = seed_from_urls(urls, log=log)

        assert result["rejected_url"] == 3
        assert result["enqueued"] == 0
        log.enqueue_many.assert_called_once_with([], source="itviec")
