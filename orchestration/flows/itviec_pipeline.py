"""TalentPulse ITviec pipeline orchestrated with Prefect.

    listing_crawl -> seed_queue -> detail_crawl -> detail_parse -> load_warehouse -> dbt_transform
"""
from __future__ import annotations

import time

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from orchestration.flows._shared import counters_table, dispatch_dashboard_alerts, fmt_duration, run_dbt, run_normalizer
from src.utils.config import config
from src.crawlers.browser import StealthBrowser
from src.crawlers.itviec.detail import ITviecDetailCrawler
from src.crawlers.itviec.listing import ITviecListingCrawler
from src.loaders.job_detail_loader import JobDetailLoader
from src.parsers.itviec.detail_parser import ITviecDetailParser
from src.queue.itviec_seeder import seed_from_urls
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient


ITVIEC_PARSED_PREFIX = "parsed/details/itviec/"


@task(name="itviec_listing_crawl", retries=1, timeout_seconds=1800)
def listing_crawl(keywords: list[str], max_pages: int | None = None) -> list[str]:
    logger = get_run_logger()
    t0 = time.time()
    with StealthBrowser() as browser:
        crawler = ITviecListingCrawler(browser=browser)
        result = crawler.crawl_all_listings(keywords=keywords, max_pages=max_pages)
    dur = time.time() - t0
    logger.info(f"Listing done: {result['counters']}")
    create_markdown_artifact(
        markdown=counters_table("itviec", "listing_crawl", result["counters"], dur),
        key="itviec-listing",
        description="ITviec listing crawl results",
    )
    return result["urls"]


@task(name="itviec_seed_queue", retries=1)
def seed_queue(urls: list[str]) -> dict:
    t0 = time.time()
    result = seed_from_urls(urls)
    dur = time.time() - t0
    get_run_logger().info(f"Seed result: {result}")
    create_markdown_artifact(
        markdown=counters_table("itviec", "seed_queue", result, dur),
        key="itviec-seed",
        description="ITviec queue seeding results",
    )
    return result


@task(name="itviec_detail_crawl", retries=0, timeout_seconds=7200)
def detail_crawl(max_jobs: int | None = None) -> dict:
    logger = get_run_logger()
    t0 = time.time()
    log = CrawlLog()
    requeued = log.requeue_stale(
        source="itviec", older_than_minutes=config.CRAWL_STALE_CLAIM_MINUTES
    )
    if requeued:
        logger.info(f"Requeued {requeued} stale in_progress rows from a previous killed run")
    with StealthBrowser() as browser:
        crawler = ITviecDetailCrawler(
            browser=browser,
            log=log,
            minio=MinioClient(),
        )
        result = crawler.run(max_jobs=max_jobs)
    dur = time.time() - t0
    logger.info(f"Detail crawl done: {result}")
    create_markdown_artifact(
        markdown=counters_table("itviec", "detail_crawl", result, dur),
        key="itviec-detail-crawl",
        description="ITviec detail crawl results",
    )
    return result


@task(name="itviec_detail_parse", retries=1)
def detail_parse(force: bool = False) -> dict:
    t0 = time.time()
    result = ITviecDetailParser().run_batch(force=force)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("itviec", "detail_parse", result, dur),
        key="itviec-parse",
        description="ITviec parse results",
    )
    return result


@task(name="itviec_load_warehouse", retries=2)
def load_warehouse() -> dict:
    t0 = time.time()
    result = JobDetailLoader().run_batch(prefix=ITVIEC_PARSED_PREFIX)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("itviec", "load_warehouse", result, dur),
        key="itviec-load",
        description="ITviec warehouse load results",
    )
    return result


@task(name="itviec_dbt_transform", retries=1, timeout_seconds=3600)
def dbt_transform() -> str:
    return run_dbt()


@task(name="itviec_normalize", retries=1, timeout_seconds=3600)
def normalize() -> str:
    return run_normalizer()


@task(name="itviec_dispatch_alerts", retries=1, timeout_seconds=180)
def dispatch_alerts() -> dict:
    return dispatch_dashboard_alerts(source="itviec_etl")


@flow(name="itviec-pipeline")
def itviec_pipeline(
    keywords: list[str] | None = None,
    max_listing_pages: int | None = None,
    detail_max_jobs: int | None = None,
    force_reparse: bool = False,
) -> dict:
    """End-to-end ITviec pipeline."""
    flow_t0 = time.time()
    keywords = keywords or config.ITVIEC_KEYWORDS
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
        "## Pipeline Summary -- ITviec\n"
        "| Stage | Result |\n|-------|--------|\n"
        f"| Listing | {len(urls)} URLs collected |\n"
        f"| Seed | {seed_result.get('enqueued', 0)} enqueued, {seed_result.get('skipped', 0)} skipped |\n"
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
        key="itviec-pipeline-summary",
        description=f"ITviec pipeline run summary ({total_dur})",
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
        itviec_pipeline.serve(
            name="itviec-pipeline-daily",
            cron="0 4 * * *",
            tags=["itviec", "etl"],
            parameters={
                "keywords": config.ITVIEC_KEYWORDS,
            },
        )
    else:
        itviec_pipeline()
