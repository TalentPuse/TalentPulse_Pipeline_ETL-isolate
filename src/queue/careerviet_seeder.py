"""Seeder: enqueue CareerViet detail URLs into raw.crawl_log."""
from __future__ import annotations

import logging
import re

from src.storage.crawl_log import CrawlLog

logger = logging.getLogger(__name__)

# CareerViet detail URLs end with the job id in hex, e.g.
#   https://careerviet.vn/vi/tim-viec-lam/senior-data-analyst.35C80246.html
# The id also appears as identifier.value in the page's JSON-LD, so the parser
# and the queue agree on the same key without either having to guess.
CAREERVIET_URL_RE = re.compile(
    r"^https://careerviet\.vn/vi/tim-viec-lam/.+\.([0-9A-Fa-f]{6,})\.html$"
)


def extract_job_id(url: str) -> str | None:
    """Return the hex job id from a CareerViet detail URL, or None."""
    m = CAREERVIET_URL_RE.match(url)
    return m.group(1).upper() if m else None


def seed_from_urls(urls: list[str], log: CrawlLog | None = None) -> dict:
    """Enqueue CareerViet detail URLs into crawl_log.

    Returns counters: enqueued, skipped, rejected_url.
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

    # One batch round-trip rather than a connection + INSERT per URL — the
    # per-row path is what made seeding crawl over the tailnet (see
    # CrawlLog.enqueue_many).
    counters["enqueued"] = log.enqueue_many(items, source="careerviet")

    logger.info(f"CareerViet seed result: {counters}")
    return counters
