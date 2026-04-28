"""TalentPulse ITviec pipeline orchestrated with Prefect.

    listing_crawl → seed_queue → detail_crawl → detail_parse → load_warehouse → dbt_transform
"""
from __future__ import annotations

import subprocess

from prefect import flow, get_run_logger, task

from src.crawlers.browser import StealthBrowser
from src.crawlers.itviec.detail import ITviecDetailCrawler
from src.crawlers.itviec.listing import ITviecListingCrawler
from src.loaders.job_detail_loader import JobDetailLoader
from src.parsers.itviec.detail_parser import ITviecDetailParser
from src.queue.itviec_seeder import seed_from_urls
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient


@task(name="itviec_listing_crawl", retries=1, timeout_seconds=1800)
def listing_crawl(keywords: list[str], max_pages: int | None = None) -> list[str]:
    logger = get_run_logger()
    with StealthBrowser() as browser:
        crawler = ITviecListingCrawler(browser=browser)
        result = crawler.crawl_all_listings(keywords=keywords, max_pages=max_pages)
    logger.info(f"Listing done: {result['counters']}")
    return result["urls"]


@task(name="itviec_seed_queue", retries=1)
def seed_queue(urls: list[str]) -> dict:
    result = seed_from_urls(urls)
    get_run_logger().info(f"Seed result: {result}")
    return result


@task(name="itviec_detail_crawl", retries=1, timeout_seconds=3600)
def detail_crawl(max_jobs: int | None = None) -> dict:
    logger = get_run_logger()
    with StealthBrowser() as browser:
        crawler = ITviecDetailCrawler(
            browser=browser,
            log=CrawlLog(),
            minio=MinioClient(),
        )
        result = crawler.run(max_jobs=max_jobs)
    logger.info(f"Detail crawl done: {result}")
    return result


@task(name="itviec_detail_parse", retries=1)
def detail_parse(force: bool = False) -> dict:
    return ITviecDetailParser().run_batch(force=force)


@task(name="itviec_load_warehouse", retries=2)
def load_warehouse() -> dict:
    return JobDetailLoader().run_batch()


@task(name="itviec_dbt_transform", retries=1, timeout_seconds=600)
def dbt_transform() -> str:
    logger = get_run_logger()
    dbt_dir = "/app/dbt_transform"
    for cmd in ["dbt seed", "dbt run"]:
        full_cmd = f"{cmd} --profiles-dir . --project-dir {dbt_dir}"
        logger.info(f"running: {full_cmd}")
        result = subprocess.run(
            full_cmd.split(),
            capture_output=True, text=True, cwd=dbt_dir,
        )
        logger.info(result.stdout[-2000:] if result.stdout else "")
        if result.returncode != 0:
            logger.error(result.stderr[-2000:] if result.stderr else "")
            raise RuntimeError(f"{cmd} failed with exit code {result.returncode}")
    return "dbt seed + run OK"


@flow(name="itviec-pipeline")
def itviec_pipeline(
    keywords: list[str] | None = None,
    max_listing_pages: int | None = None,
    detail_max_jobs: int | None = None,
    force_reparse: bool = False,
) -> dict:
    """End-to-end ITviec pipeline."""
    keywords = keywords or ["data-engineer", "ai-engineer", "data-analyst"]
    urls = listing_crawl(keywords, max_listing_pages)
    seed_result = seed_queue(urls)
    crawl_result = detail_crawl(max_jobs=detail_max_jobs)
    parse_result = detail_parse(force=force_reparse)
    load_result = load_warehouse()
    dbt_result = dbt_transform()
    return {
        "seed": seed_result,
        "crawl": crawl_result,
        "parse": parse_result,
        "load": load_result,
        "dbt": dbt_result,
    }


if __name__ == "__main__":
    import os

    if os.getenv("PREFECT_DEPLOY", "0") == "1":
        itviec_pipeline.serve(
            name="itviec-pipeline-daily",
            cron="0 4 * * *",
            tags=["itviec", "etl"],
            parameters={
                "keywords": ["data-engineer", "ai-engineer", "data-analyst"],
            },
        )
    else:
        itviec_pipeline()
