"""Tests for LinkedIn seeder — enqueue job IDs into crawl_log."""
from unittest.mock import MagicMock

from src.queue.linkedin_seeder import seed_from_job_ids


class TestSeedFromJobIds:
    def test_enqueues_valid_ids(self):
        log = MagicMock()
        log.enqueue.return_value = True
        result = seed_from_job_ids(["12345", "67890"], log=log)
        assert result["enqueued"] == 2
        assert result["skipped"] == 0
        assert log.enqueue.call_count == 2
        log.enqueue.assert_any_call("12345", "https://www.linkedin.com/jobs/view/12345", source="linkedin")
        log.enqueue.assert_any_call("67890", "https://www.linkedin.com/jobs/view/67890", source="linkedin")

    def test_skips_already_fresh(self):
        log = MagicMock()
        log.enqueue.side_effect = [False, True]
        result = seed_from_job_ids(["12345", "67890"], log=log)
        assert result["enqueued"] == 1
        assert result["skipped"] == 1

    def test_strips_whitespace(self):
        log = MagicMock()
        log.enqueue.return_value = True
        result = seed_from_job_ids(["  12345  "], log=log)
        assert result["enqueued"] == 1
        log.enqueue.assert_called_with("12345", "https://www.linkedin.com/jobs/view/12345", source="linkedin")

    def test_skips_empty_ids(self):
        log = MagicMock()
        log.enqueue.return_value = True
        result = seed_from_job_ids(["", "   ", "12345"], log=log)
        assert result["enqueued"] == 1
        assert log.enqueue.call_count == 1

    def test_empty_list(self):
        log = MagicMock()
        result = seed_from_job_ids([], log=log)
        assert result == {"enqueued": 0, "skipped": 0}
        log.enqueue.assert_not_called()

    def test_converts_int_to_string(self):
        log = MagicMock()
        log.enqueue.return_value = True
        result = seed_from_job_ids([12345], log=log)
        assert result["enqueued"] == 1
        log.enqueue.assert_called_with("12345", "https://www.linkedin.com/jobs/view/12345", source="linkedin")
