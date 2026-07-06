"""
Skill Extraction Pipeline — LLM-based skill extraction from job descriptions.

Incremental: only processes jobs not yet in raw.skill_extraction_log.
Runs as a Prefect flow, can be scheduled daily after crawl pipelines.
"""
from __future__ import annotations

import asyncio
import time

from prefect import flow, get_run_logger

from src.extractors.skill_extractor import SkillExtractor, load_config
from src.storage.db import pg_connection
from src.storage.skill_enriched_repo import (
    count_unprocessed,
    get_unprocessed_jobs,
    write_results,
)
from src.utils.config import Config as PipelineConfig
from orchestration.flows._shared import run_dbt


@flow(name="skill-extraction-pipeline")
def skill_extraction_pipeline(
    batch_size: int | None = None,
    max_jobs: int | None = None,
    run_dbt_after: bool = True,
) -> dict:
    logger = get_run_logger()
    t0 = time.time()

    config = load_config()
    extractor = SkillExtractor(config)

    if batch_size:
        extractor.batch_size = batch_size

    dsn = PipelineConfig.get_db_uri()
    total_skills = 0
    total_jobs = 0
    total_batches = 0
    total_tokens = 0

    with pg_connection(dsn) as conn:
        cur = conn.cursor()
        remaining = count_unprocessed(cur)
        cur.close()
        logger.info("Jobs to process: %d", remaining)

        while True:
            if max_jobs and total_jobs >= max_jobs:
                logger.info("Reached max_jobs limit (%d), stopping", max_jobs)
                break

            cur = conn.cursor()
            jobs = get_unprocessed_jobs(cur, limit=extractor.batch_size)
            cur.close()

            if not jobs:
                logger.info("No more jobs to process")
                break

            total_batches += 1
            logger.info(
                "Batch %d: processing %d jobs", total_batches, len(jobs),
            )

            results = asyncio.run(extractor.process_batch(jobs))

            stats = write_results(conn, results)
            total_skills += stats["n_skills"]
            total_jobs += stats["n_jobs"]
            total_tokens += sum(r.tokens_used for r in results)

            logger.info(
                "Batch %d done: %d skills, %d jobs, %d tokens",
                total_batches, stats["n_skills"], stats["n_jobs"],
                sum(r.tokens_used for r in results),
            )

    elapsed = time.time() - t0

    # Run DBT to rebuild analytics
    dbt_result = None
    if run_dbt_after and total_jobs > 0:
        logger.info("Running DBT transformations...")
        dbt_result = run_dbt()

    result = {
        "total_skills": total_skills,
        "total_jobs": total_jobs,
        "total_batches": total_batches,
        "total_tokens": total_tokens,
        "elapsed_seconds": round(elapsed, 1),
        "dbt": dbt_result,
    }

    logger.info(
        "Pipeline complete: %d skills from %d jobs in %.1fs (%d tokens)",
        total_skills, total_jobs, elapsed, total_tokens,
    )
    return result


if __name__ == "__main__":
    import os

    if os.getenv("PREFECT_DEPLOY", "0") == "1":
        skill_extraction_pipeline.serve(
            name="skill-extraction-pipeline-daily",
            cron="0 8 * * *",
            tags=["skills", "llm"],
        )
    else:
        # Run-once mode (GHA daily job): process the full backlog and
        # rebuild dbt gold tables so extracted skills actually land there.
        # SKILL_MAX_JOBS lets an operator cap a single run (e.g. for a
        # manual smoke test) without touching code; unset/0 means "all".
        max_jobs = int(os.getenv("SKILL_MAX_JOBS", "0")) or None
        result = skill_extraction_pipeline(max_jobs=max_jobs, run_dbt_after=True)
        print(result)
