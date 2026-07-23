"""Tests for LinkedIn seeder — enqueue job IDs into crawl_log."""
from unittest.mock import MagicMock

from src.queue.linkedin_seeder import seed_from_job_ids


class TestSeedFromJobIds:
    # The seeder now collects (job_id, url) pairs and enqueues them in a single
    # CrawlLog.enqueue_many() call; `enqueued` reflects that batch's return value.
    def test_enqueues_valid_ids(self):
        log = MagicMock()
        log.enqueue_many.return_value = 2
        result = seed_from_job_ids(["12345", "67890"], log=log)
        assert result["enqueued"] == 2
        log.enqueue_many.assert_called_once_with(
            [
                ("12345", "https://www.linkedin.com/jobs/view/12345"),
                ("67890", "https://www.linkedin.com/jobs/view/67890"),
            ],
            source="linkedin",
        )

    def test_strips_whitespace(self):
        log = MagicMock()
        log.enqueue_many.return_value = 1
        result = seed_from_job_ids(["  12345  "], log=log)
        assert result["enqueued"] == 1
        log.enqueue_many.assert_called_once_with(
            [("12345", "https://www.linkedin.com/jobs/view/12345")], source="linkedin"
        )

    def test_skips_empty_ids(self):
        log = MagicMock()
        log.enqueue_many.return_value = 1
        result = seed_from_job_ids(["", "   ", "12345"], log=log)
        assert result["enqueued"] == 1
        # empty/whitespace ids are dropped before the batch
        log.enqueue_many.assert_called_once_with(
            [("12345", "https://www.linkedin.com/jobs/view/12345")], source="linkedin"
        )

    def test_empty_list(self):
        log = MagicMock()
        log.enqueue_many.return_value = 0
        result = seed_from_job_ids([], log=log)
        assert result["enqueued"] == 0
        log.enqueue_many.assert_called_once_with([], source="linkedin")

    def test_converts_int_to_string(self):
        log = MagicMock()
        log.enqueue_many.return_value = 1
        result = seed_from_job_ids([12345], log=log)
        assert result["enqueued"] == 1
        log.enqueue_many.assert_called_once_with(
            [("12345", "https://www.linkedin.com/jobs/view/12345")], source="linkedin"
        )
