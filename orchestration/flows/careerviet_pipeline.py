"""TalentPulse CareerViet pipeline orchestrated with Prefect.

    listing_crawl -> seed_queue -> detail_crawl -> detail_parse -> load_warehouse
                  -> normalize -> dbt_transform -> dispatch_alerts

Runs on the standard `worker` image: CareerViet serves complete HTML to a plain
HTTP client, so unlike ITviec and TopCV this needs no Playwright and no Chromium.
"""
from __future__ import annotations

import time

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from orchestration.flows._shared import (
    counters_table,
    dispatch_dashboard_alerts,
    fmt_duration,
    run_dbt,
    run_normalizer,
)
from src.crawlers.careerviet.detail import CareerVietDetailCrawler
from src.crawlers.careerviet.listing import CareerVietListingCrawler
from src.loaders.job_detail_loader import JobDetailLoader
from src.parsers.careerviet.detail_parser import CareerVietDetailParser
from src.queue.careerviet_seeder import seed_from_urls
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient
from src.utils.config import config

CAREERVIET_PARSED_PREFIX = "parsed/details/careerviet/"


@task(name="careerviet_listing_crawl", retries=1, timeout_seconds=1800)
def listing_crawl(keywords: list[str], max_pages: int | None = None) -> list[str]:
    logger = get_run_logger()
    t0 = time.time()
    result = CareerVietListingCrawler().crawl_all_listings(keywords=keywords, max_pages=max_pages)
    dur = time.time() - t0
    logger.info(f"Listing done: {result['counters']}")
    create_markdown_artifact(
        markdown=counters_table("careerviet", "listing_crawl", result["counters"], dur),
        key="careerviet-listing",
        description="CareerViet listing crawl results",
    )
    return result["urls"]


@task(name="careerviet_seed_queue", retries=1)
def seed_queue(urls: list[str]) -> dict:
    t0 = time.time()
    result = seed_from_urls(urls)
    dur = time.time() - t0
    get_run_logger().info(f"Seed result: {result}")
    create_markdown_artifact(
        markdown=counters_table("careerviet", "seed_queue", result, dur),
        key="careerviet-seed",
        description="CareerViet queue seeding results",
    )
    return result


# retries=0 deliberately. A retried crawl task can run while the first attempt is
# still alive after a Prefect heartbeat lapse; both then drain raw.crawl_log and
# the zombie claims rows it never resolves. That stranded 3,684 LinkedIn rows
# before linkedin_detail_crawl was dropped to 0 as well.
@task(name="careerviet_detail_crawl", retries=0, timeout_seconds=7200)
def detail_crawl(max_jobs: int | None = None) -> dict:
    logger = get_run_logger()
    t0 = time.time()
    log = CrawlLog()
    requeued = log.requeue_stale(
        source="careerviet", older_than_minutes=config.CRAWL_STALE_CLAIM_MINUTES
    )
    if requeued:
        logger.info(f"Requeued {requeued} stale in_progress rows from a previous killed run")
    try:
        result = CareerVietDetailCrawler(log=log, minio=MinioClient()).run(max_jobs=max_jobs)
    finally:
        log.close()
    dur = time.time() - t0
    logger.info(f"Detail crawl done in {fmt_duration(dur)}: {result}")
    create_markdown_artifact(
        markdown=counters_table("careerviet", "detail_crawl", result, dur),
        key="careerviet-detail-crawl",
        description="CareerViet detail crawl results",
    )
    return result


@task(name="careerviet_detail_parse", retries=1)
def detail_parse(force: bool = False) -> dict:
    t0 = time.time()
    result = CareerVietDetailParser().run_batch(force=force)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("careerviet", "detail_parse", result, dur),
        key="careerviet-parse",
        description="CareerViet parse results",
    )
    return result


@task(name="careerviet_load_warehouse", retries=2)
def load_warehouse() -> dict:
    t0 = time.time()
    result = JobDetailLoader().run_batch(prefix=CAREERVIET_PARSED_PREFIX)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("careerviet", "load_warehouse", result, dur),
        key="careerviet-load",
        description="CareerViet warehouse load results",
    )
    return result


@task(name="careerviet_normalize", retries=1, timeout_seconds=3600)
def normalize() -> str:
    return run_normalizer()


@task(name="careerviet_dbt_transform", retries=1, timeout_seconds=3600)
def dbt_transform() -> str:
    return run_dbt()


@task(name="careerviet_dispatch_alerts", retries=1, timeout_seconds=180)
def dispatch_alerts() -> dict:
    return dispatch_dashboard_alerts(source="careerviet_etl")


@flow(name="careerviet-pipeline")
def careerviet_pipeline(
    keywords: list[str] | None = None,
    max_listing_pages: int | None = None,
    detail_max_jobs: int | None = None,
    force_reparse: bool = False,
) -> dict:
    """End-to-end CareerViet pipeline."""
    flow_t0 = time.time()
    keywords = keywords or config.CAREERVIET_KEYWORDS
    urls = listing_crawl(keywords, max_listing_pages)
    seed_result = seed_queue(urls)
    crawl_result = detail_crawl(max_jobs=detail_max_jobs)
    parse_result = detail_parse(force=force_reparse)
    load_result = load_warehouse()
    norm_result = normalize()
    dbt_result = dbt_transform()
    alert_result = dispatch_alerts()

    total_dur = fmt_duration(time.time() - flow_t0)
    summary = (
        "## Pipeline Summary -- CareerViet\n"
        "| Stage | Result |\n|-------|--------|\n"
        f"| Listing | {len(urls)} URLs collected |\n"
        f"| Seed | {seed_result.get('enqueued', 0)} enqueued, "
        f"{seed_result.get('rejected_url', 0)} rejected |\n"
        f"| Detail Crawl | {crawl_result.get('success', 0)} success, "
        f"{crawl_result.get('failed', 0)} failed |\n"
        f"| Parse | {parse_result.get('success', 0)} success, "
        f"{parse_result.get('failed', 0)} failed |\n"
        f"| Load | {load_result.get('loaded', 0)} loaded, "
        f"{load_result.get('rejected', 0)} rejected |\n"
        f"| Normalize | {norm_result} |\n"
        f"| dbt | {dbt_result} |\n"
        f"| Alerts | {alert_result.get('dispatched', 0)} dispatched |\n"
        f"| **Total Duration** | **{total_dur}** |"
    )
    create_markdown_artifact(
        markdown=summary,
        key="careerviet-pipeline-summary",
        description=f"CareerViet pipeline run summary ({total_dur})",
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
        careerviet_pipeline.serve(
            name="careerviet-pipeline-daily",
            cron="0 3 * * *",
            tags=["careerviet", "etl"],
            parameters={"keywords": config.CAREERVIET_KEYWORDS},
        )
    else:
        careerviet_pipeline()
