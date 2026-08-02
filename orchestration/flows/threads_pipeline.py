"""TalentPulse Threads (Meta) pipeline orchestrated with Prefect.

    keyword_search -> post_parse -> [load_warehouse -> normalize -> dbt -> alerts]

Two deliberate differences from every other pipeline in this repo:

1. **No seed_queue stage.** The other sources crawl a listing to get URLs, queue
   them in `raw.crawl_log`, then fetch each detail page. The Threads keyword
   search returns the post text in the search response itself, so there is
   nothing to enqueue — a queue here would be an indirection with no fetch on
   the other side. There is intentionally no `src/queue/threads_seeder.py`.

2. **The tail of the pipeline is off by default** (`load_to_warehouse=False`).
   A Threads post has no title and no company, and
   `src/loaders/validators.py::validate_business_rules` rejects payloads missing
   either — so with validation on, 100% of threads rows land in
   `raw.job_detail_rejects` and none in `raw.job_detail`. Turning the loader on
   before that schema question is settled would either quarantine everything or,
   with validation off, push NULL-title/NULL-company rows into the gold marts.
   See the module docstring of `src/parsers/threads/post_parser.py` for the two
   ways out. Until one is chosen, this flow crawls, archives and parses — which
   is the part that has to be ready the moment the permission lands — and stops.

Also worth remembering while reading run output: **until Meta approves the
`threads_keyword_search` permission, the endpoint only returns posts owned by
the authenticated user.** A run that finds nothing is expected today.

Runs on the standard `worker` image: this is a JSON API over `requests`, no
browser, no Playwright.
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
from src.crawlers.threads.search import ThreadsSearchCrawler
from src.loaders.job_detail_loader import JobDetailLoader
from src.parsers.threads.post_parser import ThreadsPostParser
from src.utils.config import config

THREADS_PARSED_PREFIX = "parsed/details/threads/"


# retries=0 on the fetch-heavy task, same reason as the other sources: a retry
# can start while the first attempt is still alive after a Prefect heartbeat
# lapse. Here the cost is not stranded queue rows but a doubled spend against
# the 2,200-queries-per-24h budget, which would throttle the token for the rest
# of the day.
@task(name="threads_keyword_search", retries=0, timeout_seconds=3600)
def keyword_search(keywords: list[str], max_pages: int | None = None) -> dict:
    logger = get_run_logger()
    t0 = time.time()
    result = ThreadsSearchCrawler().search_all(keywords=keywords, max_pages=max_pages)
    dur = time.time() - t0
    counters = result["counters"]
    logger.info(f"Threads search done: {counters}")

    if counters["empty_keywords"]:
        # Loud on purpose. An empty array from this endpoint is ambiguous: it is
        # what Meta returns for keywords it deems sensitive/offensive, with HTTP
        # 200 and no reason code, and it is also what a quiet day looks like.
        # A source in this repo once died unnoticed for 7 days; ambiguity gets
        # escalated, not swallowed.
        logger.error(
            f"{counters['empty_keywords']}/{counters['keywords']} keywords returned an "
            f"EMPTY ARRAY: {result['empty_keyword_list']}. This may be Meta's silent "
            f"sensitive-keyword block, not an absence of job posts. Verify by hand."
        )
    if counters["budget_exhausted"]:
        logger.error(
            f"Daily query budget ({counters['queries_used']}) exhausted mid-run; "
            f"some keywords were never searched."
        )

    create_markdown_artifact(
        markdown=counters_table("threads", "keyword_search", counters, dur),
        key="threads-search",
        description="Threads keyword search results",
    )
    return result


@task(name="threads_post_parse", retries=1, timeout_seconds=1800)
def post_parse(force: bool = False) -> dict:
    t0 = time.time()
    result = ThreadsPostParser().run_batch(force=force)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("threads", "post_parse", result, dur),
        key="threads-parse",
        description="Threads parse results",
    )
    return result


@task(name="threads_load_warehouse", retries=2)
def load_warehouse() -> dict:
    logger = get_run_logger()
    t0 = time.time()
    result = JobDetailLoader().run_batch(prefix=THREADS_PARSED_PREFIX)
    dur = time.time() - t0
    if result.get("rejected") and not result.get("loaded"):
        logger.error(
            f"Every threads row was rejected ({result['rejected']}). Expected while "
            f"JobDetail requires title + company_name — see "
            f"src/parsers/threads/post_parser.py. This source needs its own table or "
            f"a source-aware validator before the loader is worth running."
        )
    create_markdown_artifact(
        markdown=counters_table("threads", "load_warehouse", result, dur),
        key="threads-load",
        description="Threads warehouse load results",
    )
    return result


@task(name="threads_normalize", retries=1, timeout_seconds=3600)
def normalize() -> str:
    return run_normalizer()


@task(name="threads_dbt_transform", retries=1, timeout_seconds=3600)
def dbt_transform() -> str:
    return run_dbt()


@task(name="threads_dispatch_alerts", retries=1, timeout_seconds=180)
def dispatch_alerts() -> dict:
    return dispatch_dashboard_alerts(source="threads_etl")


@flow(name="threads-pipeline")
def threads_pipeline(
    keywords: list[str] | None = None,
    max_pages: int | None = None,
    force_reparse: bool = False,
    load_to_warehouse: bool = False,
) -> dict:
    """End-to-end Threads pipeline.

    `load_to_warehouse` defaults to False on purpose — see the module docstring.
    """
    flow_t0 = time.time()
    logger = get_run_logger()
    keywords = keywords or config.THREADS_KEYWORDS

    search_result = keyword_search(keywords, max_pages)
    counters = search_result["counters"]
    parse_result = post_parse(force=force_reparse)

    load_result: dict = {}
    norm_result = dbt_result = "skipped"
    alert_result: dict = {}
    if load_to_warehouse:
        load_result = load_warehouse()
        norm_result = normalize()
        dbt_result = dbt_transform()
        alert_result = dispatch_alerts()
    else:
        logger.warning(
            "load_to_warehouse=False: stopping after parse. Threads posts carry no "
            "title/company, so loading them would either quarantine every row or push "
            "NULL-company rows into the gold marts. Flip this once the source has its "
            "own table or a source-aware validator."
        )

    total_dur = fmt_duration(time.time() - flow_t0)
    empty_note = (
        f"{counters['empty_keywords']} EMPTY (possible sensitive-keyword block)"
        if counters["empty_keywords"]
        else "none"
    )
    summary = (
        "## Pipeline Summary -- Threads\n"
        "| Stage | Result |\n|-------|--------|\n"
        f"| Search | {counters['posts_unique']} unique posts from "
        f"{counters['keywords']} keywords |\n"
        f"| Empty keywords | {empty_note} |\n"
        f"| Query budget | {counters['queries_used']} used"
        f"{', EXHAUSTED' if counters['budget_exhausted'] else ''} |\n"
        f"| Parse | {parse_result.get('success', 0)} success, "
        f"{parse_result.get('failed', 0)} failed |\n"
        f"| Load | {load_result.get('loaded', 0)} loaded, "
        f"{load_result.get('rejected', 0)} rejected"
        f"{'' if load_to_warehouse else ' (skipped: load_to_warehouse=False)'} |\n"
        f"| Normalize | {norm_result} |\n"
        f"| dbt | {dbt_result} |\n"
        f"| Alerts | {alert_result.get('dispatched', 0)} dispatched |\n"
        f"| **Total Duration** | **{total_dur}** |"
    )
    create_markdown_artifact(
        markdown=summary,
        key="threads-pipeline-summary",
        description=f"Threads pipeline run summary ({total_dur})",
    )

    return {
        "search": counters,
        "empty_keywords": search_result["empty_keyword_list"],
        "parse": parse_result,
        "load": load_result,
        "normalize": norm_result,
        "dbt": dbt_result,
        "alerts": alert_result,
    }


if __name__ == "__main__":
    import os

    if os.getenv("PREFECT_DEPLOY", "0") == "1":
        threads_pipeline.serve(
            name="threads-pipeline-daily",
            cron="0 4 * * *",
            tags=["threads", "etl"],
            parameters={"keywords": config.THREADS_KEYWORDS},
        )
    else:
        threads_pipeline()
