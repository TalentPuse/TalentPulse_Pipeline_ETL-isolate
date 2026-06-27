"""TalentPulse LinkedIn pipeline orchestrated with Prefect.

    listing_crawl -> seed_queue -> detail_crawl -> detail_parse -> load_warehouse -> dbt_transform -> dispatch_alerts
"""
from __future__ import annotations

import time

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from orchestration.flows._shared import counters_table, dispatch_dashboard_alerts, fmt_duration, run_dbt, run_normalizer
from src.utils.config import config
from src.crawlers.linkedin.detail import LinkedInDetailCrawler
from src.crawlers.linkedin.listing import LinkedInListingCrawler
from src.loaders.job_detail_loader import JobDetailLoader
from src.parsers.linkedin.detail_parser import LinkedInDetailParser
from src.queue.linkedin_seeder import seed_from_job_ids
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient


LINKEDIN_PARSED_PREFIX = "parsed/details/linkedin/"


@task(name="linkedin_listing_crawl", retries=1, timeout_seconds=3600)
def listing_crawl(keywords: list[str], max_pages: int | None = None) -> list[str]:
    logger = get_run_logger()
    t0 = time.time()
    crawler = LinkedInListingCrawler()
    result = crawler.crawl_all_listings(keywords=keywords, max_pages=max_pages)
    dur = time.time() - t0
    logger.info(f"Listing done: {result['counters']}")
    create_markdown_artifact(
        markdown=counters_table("linkedin", "listing_crawl", result["counters"], dur),
        key="linkedin-listing",
        description="LinkedIn listing crawl results",
    )
    return result["job_ids"]


@task(name="linkedin_seed_queue", retries=1)
def seed_queue(job_ids: list[str]) -> dict:
    t0 = time.time()
    result = seed_from_job_ids(job_ids)
    dur = time.time() - t0
    get_run_logger().info(f"Seed result: {result}")
    create_markdown_artifact(
        markdown=counters_table("linkedin", "seed_queue", result, dur),
        key="linkedin-seed",
        description="LinkedIn queue seeding results",
    )
    return result


@task(name="linkedin_detail_crawl", retries=1, timeout_seconds=7200)
def detail_crawl(max_jobs: int | None = None) -> dict:
    t0 = time.time()
    crawler = LinkedInDetailCrawler(log=CrawlLog(), minio=MinioClient())
    result = crawler.run(max_jobs=max_jobs)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("linkedin", "detail_crawl", result, dur),
        key="linkedin-detail-crawl",
        description="LinkedIn detail crawl results",
    )
    return result


@task(name="linkedin_detail_parse", retries=1)
def detail_parse(force: bool = False) -> dict:
    t0 = time.time()
    result = LinkedInDetailParser().run_batch(force=force)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("linkedin", "detail_parse", result, dur),
        key="linkedin-parse",
        description="LinkedIn parse results",
    )
    return result


@task(name="linkedin_load_warehouse", retries=2)
def load_warehouse() -> dict:
    t0 = time.time()
    result = JobDetailLoader().run_batch(prefix=LINKEDIN_PARSED_PREFIX)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("linkedin", "load_warehouse", result, dur),
        key="linkedin-load",
        description="LinkedIn warehouse load results",
    )
    return result


@task(name="linkedin_dbt_transform", retries=1, timeout_seconds=600)
def dbt_transform() -> str:
    return run_dbt()


@task(name="linkedin_normalize", retries=1, timeout_seconds=600)
def normalize() -> str:
    return run_normalizer()


@task(name="linkedin_dispatch_alerts", retries=1, timeout_seconds=180)
def dispatch_alerts() -> dict:
    return dispatch_dashboard_alerts(source="linkedin_etl")


@flow(name="linkedin-pipeline")
def linkedin_pipeline(
    keywords: list[str] | None = None,
    max_listing_pages: int | None = None,
    detail_max_jobs: int | None = None,
    force_reparse: bool = False,
) -> dict:
    """End-to-end LinkedIn pipeline."""
    flow_t0 = time.time()
    keywords = keywords or config.LINKEDIN_KEYWORDS
    job_ids = listing_crawl(keywords, max_listing_pages)
    seed_result = seed_queue(job_ids)
    crawl_result = detail_crawl(max_jobs=detail_max_jobs)
    parse_result = detail_parse(force=force_reparse)
    load_result = load_warehouse()
    norm_result = normalize()
    dbt_result = dbt_transform()
    alert_result = dispatch_alerts()

    total_dur = fmt_duration(time.time() - flow_t0)
    summary = (
        "## Pipeline Summary -- LinkedIn\n"
        "| Stage | Result |\n|-------|--------|\n"
        f"| Listing | {len(job_ids)} job IDs collected |\n"
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
        key="linkedin-pipeline-summary",
        description=f"LinkedIn pipeline run summary ({total_dur})",
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
        linkedin_pipeline.serve(
            name="linkedin-pipeline-daily",
            cron="0 6 * * *",
            tags=["linkedin", "etl"],
            parameters={
                "keywords": config.LINKEDIN_KEYWORDS,
            },
        )
    else:
        linkedin_pipeline()
