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
    def test_enqueues_valid_urls(self):
        log = MagicMock()
        log.enqueue.return_value = True

        urls = [
            "https://itviec.com/it-jobs/data-engineer-acme-4611",
            "https://itviec.com/it-jobs/ai-engineer-bigcorp-2549",
        ]
        result = seed_from_urls(urls, log=log)

        assert result["enqueued"] == 2
        assert result["skipped"] == 0
        assert result["rejected_url"] == 0
        assert log.enqueue.call_count == 2

        first_call = log.enqueue.call_args_list[0]
        assert first_call[0] == ("4611", urls[0])
        assert first_call[1] == {"source": "itviec"}

    def test_counts_rejected_urls(self):
        log = MagicMock()
        urls = [
            "https://itviec.com/it-jobs/valid-job-111",
            "https://evil.com/fake-4611",
            "not-even-a-url",
        ]
        log.enqueue.return_value = True
        result = seed_from_urls(urls, log=log)

        assert result["enqueued"] == 1
        assert result["rejected_url"] == 2

    def test_counts_skipped_when_enqueue_returns_false(self):
        log = MagicMock()
        log.enqueue.return_value = False  # already fresh

        urls = ["https://itviec.com/it-jobs/dup-job-999"]
        result = seed_from_urls(urls, log=log)

        assert result["enqueued"] == 0
        assert result["skipped"] == 1

    def test_empty_list(self):
        log = MagicMock()
        result = seed_from_urls([], log=log)

        assert result == {"enqueued": 0, "skipped": 0, "rejected_url": 0}
        log.enqueue.assert_not_called()

    def test_all_invalid(self):
        log = MagicMock()
        urls = ["bad", "worse", "https://other.com/123"]
        result = seed_from_urls(urls, log=log)

        assert result["rejected_url"] == 3
        assert result["enqueued"] == 0
        log.enqueue.assert_not_called()

    def test_mixed_enqueue_and_skip(self):
        log = MagicMock()
        log.enqueue.side_effect = [True, False, True]

        urls = [
            "https://itviec.com/it-jobs/job-a-111",
            "https://itviec.com/it-jobs/job-b-222",
            "https://itviec.com/it-jobs/job-c-333",
        ]
        result = seed_from_urls(urls, log=log)

        assert result["enqueued"] == 2
        assert result["skipped"] == 1
        assert result["rejected_url"] == 0
