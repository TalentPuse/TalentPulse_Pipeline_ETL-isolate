"""Seeder: enqueue TopCV detail URLs into raw.crawl_log."""
from __future__ import annotations

import logging
import re

from src.storage.crawl_log import CrawlLog

logger = logging.getLogger(__name__)

# TopCV detail URLs: https://www.topcv.vn/viec-lam/{slug}/{numeric_id}.html[?query]
TOPCV_URL_RE = re.compile(
    r"^https://www\.topcv\.vn/viec-lam/[\w\-]+/(\d+)\.html(?:\?.*)?$"
)


def extract_job_id(url: str) -> str | None:
    """Extract numeric job ID from a TopCV detail URL, ignoring any query string."""
    m = TOPCV_URL_RE.match(url)
    return m.group(1) if m else None


def canonical_url(url: str) -> str:
    """Drop the query string so stored URLs are stable and dedup-friendly."""
    return url.split("?", 1)[0]


def seed_from_urls(urls: list[str], log: CrawlLog | None = None) -> dict:
    """Enqueue TopCV detail URLs into crawl_log.

    Returns a counter dict: enqueued, skipped, rejected_url.
    """
    log = log or CrawlLog()
    counters = {"enqueued": 0, "skipped": 0, "rejected_url": 0}

    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for url in urls:
        job_id = extract_job_id(url)
        if not job_id:
            counters["rejected_url"] += 1
            logger.debug(f"Rejected URL (no job_id): {url}")
            continue
        if job_id in seen:
            continue
        seen.add(job_id)
        items.append((job_id, canonical_url(url)))

    counters["enqueued"] = log.enqueue_many(items, source="topcv")
    logger.info(f"TopCV seed result: {counters}")
    return counters
