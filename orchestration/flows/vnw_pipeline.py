"""TalentPulse VietnamWorks pipeline orchestrated with Prefect.

Wires existing entry points into a single flow:
    listing_crawl -> seed_queue -> detail_crawl -> detail_parse -> load_warehouse -> dbt_transform
"""
from __future__ import annotations

import time

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from orchestration.flows._shared import counters_table, dispatch_dashboard_alerts, fmt_duration, run_dbt, run_normalizer
from src.utils.config import config
from src.crawlers.vietnamworks.detail.detail_crawler import DetailCrawler
from src.crawlers.vietnamworks.detail.fetcher import Fetcher
from src.crawlers.vietnamworks.listing import VietnamWorksListingCrawler
from src.loaders.job_detail_loader import JobDetailLoader
from src.parsers.vietnamworks.detail.detail_parser import DetailParser
from src.queue.seeder import seed_from_listings
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient


@task(name="listing_crawl", retries=2, retry_delay_seconds=60)
def listing_crawl(keywords: list[str], max_pages: int = 1) -> int:
    logger = get_run_logger()
    t0 = time.time()
    crawler = VietnamWorksListingCrawler()
    pages = 0
    for kw in keywords:
        crawler.crawl_all_listings(keyword=kw, max_pages=max_pages)
        pages += max_pages
        logger.info(f"listing done: keyword='{kw}' pages={max_pages}")
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("vnw", "listing_crawl", {"keywords": len(keywords), "pages": pages}, dur),
        key="vnw-listing",
        description="VNW listing crawl results",
    )
    return pages


@task(name="seed_queue", retries=1)
def seed_queue() -> dict:
    t0 = time.time()
    result = seed_from_listings()
    dur = time.time() - t0
    get_run_logger().info(f"seed result: {result}")
    create_markdown_artifact(
        markdown=counters_table("vnw", "seed_queue", result, dur),
        key="vnw-seed",
        description="VNW queue seeding results",
    )
    return result


@task(name="detail_crawl", retries=2, retry_delay_seconds=120, timeout_seconds=3600)
def detail_crawl(max_jobs: int | None = None) -> dict:
    t0 = time.time()
    crawler = DetailCrawler(log=CrawlLog(), fetcher=Fetcher(), minio=MinioClient())
    result = crawler.run(max_jobs=max_jobs)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("vnw", "detail_crawl", result, dur),
        key="vnw-detail-crawl",
        description="VNW detail crawl results",
    )
    return result


@task(name="detail_parse", retries=1)
def detail_parse(force: bool = False) -> dict:
    t0 = time.time()
    result = DetailParser().run_batch(force=force)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("vnw", "detail_parse", result, dur),
        key="vnw-parse",
        description="VNW parse results",
    )
    return result


@task(name="load_warehouse", retries=2)
def load_warehouse() -> dict:
    t0 = time.time()
    result = JobDetailLoader().run_batch()
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("vnw", "load_warehouse", result, dur),
        key="vnw-load",
        description="VNW warehouse load results",
    )
    return result


@task(name="dbt_transform", retries=1, timeout_seconds=600)
def dbt_transform() -> str:
    return run_dbt()


@task(name="normalize", retries=1, timeout_seconds=600)
def normalize() -> str:
    return run_normalizer()


@task(name="dispatch_alerts", retries=1, timeout_seconds=180)
def dispatch_alerts() -> dict:
    return dispatch_dashboard_alerts()


@flow(name="vnw-pipeline")
def vnw_pipeline(
    keywords: list[str] | None = None,
    listing_pages: int = 1,
    detail_max_jobs: int | None = None,
    force_reparse: bool = False,
) -> dict:
    """End-to-end VietnamWorks pipeline."""
    flow_t0 = time.time()
    keywords = keywords or config.VNW_KEYWORDS
    listing_crawl(keywords, listing_pages)
    seed_result = seed_queue()
    crawl_result = detail_crawl(max_jobs=detail_max_jobs)
    parse_result = detail_parse(force=force_reparse)
    load_result = load_warehouse()
    norm_result = normalize()
    dbt_result = dbt_transform()
    alert_result = dispatch_alerts()

    total_dur = fmt_duration(time.time() - flow_t0)
    summary = (
        "## Pipeline Summary -- VietnamWorks\n"
        "| Stage | Result |\n|-------|--------|\n"
        f"| Listing | {len(keywords)} keywords, {listing_pages} pages each |\n"
        f"| Seed | {seed_result} |\n"
        f"| Detail Crawl | {crawl_result.get('success', 0)} success, {crawl_result.get('failed', 0)} failed |\n"
        f"| Parse | {parse_result.get('success', 0)} success, {parse_result.get('failed', 0)} failed |\n"
        f"| Load | {load_result.get('loaded', 0)} loaded, {load_result.get('rejected', 0)} rejected |\n"
        f"| Normalize | {norm_result} |\n"
        f"| dbt | {dbt_result} |\n"
        f"| Alerts | {alert_result.get('dispatched', 0)} dispatched |\n"
        f"| **Total Duration** | **{total_dur}** |"
    )
    create_markdown_artifact(
        markdown=summary,
        key="vnw-pipeline-summary",
        description=f"VNW pipeline run summary ({total_dur})",
    )

    return {
        "seed": seed_result,
        "crawl": crawl_result,
        "parse": parse_result,
        "load": load_result,
        "normalize": norm_result,
        "dbt": dbt_result,
        "alerts": alert_result,
    }


if __name__ == "__main__":
    import os

    if os.getenv("PREFECT_DEPLOY", "0") == "1":
        vnw_pipeline.serve(
            name="vnw-pipeline-daily",
            cron="0 2 * * *",
            tags=["vietnamworks", "etl"],
            parameters={
                "keywords": config.VNW_KEYWORDS,
                "listing_pages": 3,
            },
        )
    else:
        vnw_pipeline()
