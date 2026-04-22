"""TalentPulse VietnamWorks pipeline orchestrated with Prefect.

Wires existing entry points into a single flow:
    listing_crawl → seed_queue → detail_crawl → detail_parse → load_warehouse
"""
from __future__ import annotations

import sys

from prefect import flow, get_run_logger, task

from src.crawlers.vietnamworks.detail.detail_crawler import main as detail_crawler_main
from src.crawlers.vietnamworks.listing import VietnamWorksListingCrawler
from src.loaders.job_detail_loader import JobDetailLoader
from src.parsers.vietnamworks.detail.detail_parser import DetailParser
from src.queue.seeder import seed_from_listings


@task(name="listing_crawl", retries=2, retry_delay_seconds=60)
def listing_crawl(keywords: list[str], max_pages: int = 1) -> int:
    logger = get_run_logger()
    crawler = VietnamWorksListingCrawler()
    pages = 0
    for kw in keywords:
        crawler.crawl_all_listings(keyword=kw, max_pages=max_pages)
        pages += max_pages
        logger.info(f"listing done: keyword='{kw}' pages={max_pages}")
    return pages


@task(name="seed_queue", retries=1)
def seed_queue() -> dict:
    result = seed_from_listings()
    get_run_logger().info(f"seed result: {result}")
    return result


@task(name="detail_crawl", retries=2, retry_delay_seconds=120, timeout_seconds=3600)
def detail_crawl(max_jobs: int | None = None) -> None:
    """Reuse the CLI entry by faking sys.argv (the simplest no-refactor path)."""
    argv = ["detail_crawler"]
    if max_jobs:
        argv += ["--max-jobs", str(max_jobs)]
    sys.argv = argv
    detail_crawler_main()


@task(name="detail_parse", retries=1)
def detail_parse(force: bool = False) -> dict:
    return DetailParser().run_batch(force=force)


@task(name="load_warehouse", retries=2)
def load_warehouse() -> dict:
    return JobDetailLoader().run_batch()


@flow(name="vnw-pipeline")
def vnw_pipeline(
    keywords: list[str] | None = None,
    listing_pages: int = 1,
    detail_max_jobs: int | None = None,
    force_reparse: bool = False,
) -> dict:
    """End-to-end VietnamWorks pipeline.

    Args:
        keywords: Search terms for listing API. Defaults to DE/AI focus.
        listing_pages: Pages per keyword to crawl.
        detail_max_jobs: Cap detail crawl batch size (None = all pending).
        force_reparse: Re-parse even if parsed JSON exists in MinIO.
    """
    keywords = keywords or ["Data Engineer", "AI Engineer"]
    listing_crawl(keywords, listing_pages)
    seed_result = seed_queue()
    detail_crawl(max_jobs=detail_max_jobs)
    parse_result = detail_parse(force=force_reparse)
    load_result = load_warehouse()
    return {
        "seed": seed_result,
        "parse": parse_result,
        "load": load_result,
    }


if __name__ == "__main__":
    vnw_pipeline()
