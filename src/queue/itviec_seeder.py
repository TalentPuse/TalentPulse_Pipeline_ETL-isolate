"""Seeder: enqueue ITviec detail URLs into raw.crawl_log."""
from __future__ import annotations

import logging
import re

from src.storage.crawl_log import CrawlLog

logger = logging.getLogger(__name__)

ITVIEC_URL_RE = re.compile(
    r"^https://itviec\.com/it-jobs/[\w\-]+-(\d+)$"
)


def extract_job_id(url: str) -> str | None:
    """Extract numeric job ID from ITviec detail URL.

    URL format: https://itviec.com/it-jobs/{slug}-{company}-{numeric_id}
    """
    m = ITVIEC_URL_RE.match(url)
    return m.group(1) if m else None


def seed_from_urls(urls: list[str], log: CrawlLog | None = None) -> dict:
    """Enqueue ITviec detail URLs into crawl_log.

    Args:
        urls: List of full ITviec detail URLs.
        log: CrawlLog instance (created if None).

    Returns:
        Counter dict: enqueued, skipped, rejected_url.
    """
    log = log or CrawlLog()
    counters = {"enqueued": 0, "skipped": 0, "rejected_url": 0}

    items: list[tuple[str, str]] = []
    for url in urls:
        job_id = extract_job_id(url)
        if not job_id:
            counters["rejected_url"] += 1
            logger.debug(f"Rejected URL (no job_id): {url}")
            continue
        items.append((job_id, url))

    # One batch round-trip instead of a connection + INSERT per URL (see
    # CrawlLog.enqueue_many) — the per-row path made seeding slow over the tailnet.
    counters["enqueued"] = log.enqueue_many(items, source="itviec")

    logger.info(f"ITviec seed result: {counters}")
    return counters
