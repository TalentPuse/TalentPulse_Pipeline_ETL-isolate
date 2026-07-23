"""Seeder: enqueue LinkedIn job IDs into raw.crawl_log."""
from __future__ import annotations

import logging

from src.storage.crawl_log import CrawlLog

logger = logging.getLogger(__name__)

VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}"


def seed_from_job_ids(job_ids: list[str], log: CrawlLog | None = None) -> dict:
    """Enqueue LinkedIn job IDs into crawl_log.

    Args:
        job_ids: List of LinkedIn numeric job IDs.
        log: CrawlLog instance (created if None).

    Returns:
        Counter dict: enqueued, skipped.
    """
    log = log or CrawlLog()
    counters = {"enqueued": 0, "skipped": 0}

    items: list[tuple[str, str]] = []
    for job_id in job_ids:
        job_id = str(job_id).strip()
        if not job_id:
            continue
        items.append((job_id, VIEW_URL.format(job_id=job_id)))

    # One batch round-trip instead of a connection + INSERT per id (see
    # CrawlLog.enqueue_many).
    counters["enqueued"] = log.enqueue_many(items, source="linkedin")

    logger.info(f"LinkedIn seed result: {counters}")
    return counters
