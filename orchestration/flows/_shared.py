"""Shared helpers for Prefect pipeline flows."""
from __future__ import annotations

import os
import subprocess

import requests
from prefect import get_run_logger
from prefect.artifacts import create_markdown_artifact


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def counters_table(source: str, stage: str, counters: dict, duration: float) -> str:
    rows = "\n".join(f"| {k} | {v} |" for k, v in counters.items())
    return (
        f"| Metric | Value |\n|--------|-------|\n"
        f"| Source | {source} |\n"
        f"| Stage | {stage} |\n"
        f"{rows}\n"
        f"| Duration | {fmt_duration(duration)} |"
    )


def dispatch_dashboard_alerts(source: str = "etl_inline") -> dict:
    """Call dashboard API to dispatch job alerts after fresh data is loaded.

    Args:
        source: Which trigger source initiated this dispatch
                ("background_loop", "admin_manual", "cron_webhook", "etl_inline",
                 "vnw_etl", "itviec_etl", "linkedin_etl", "alert_dispatch_flow")
    """
    logger = get_run_logger()
    url = os.getenv("DASHBOARD_API_URL", "http://tp-backend:8001")
    secret = os.getenv("ALERT_DISPATCH_SECRET") or os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    try:
        resp = requests.post(
            f"{url}/api/admin/alerts/dispatch-internal",
            headers={
                "X-Webhook-Secret": secret,
                "X-Dispatch-Source": source,  # NEW header
            },
            timeout=120,
        )
        resp.raise_for_status()
        result = resp.json()
        dispatched = result.get("dispatched", 0)
        logger.info(f"Dashboard alerts dispatched: {dispatched} jobs (source={source})")
        return result
    except requests.exceptions.HTTPError as exc:
        logger.error(f"Alert dispatch HTTP error: {exc.response.status_code} — {exc.response.text[:200]}")
        return {"dispatched": 0, "error": str(exc)}
    except Exception as exc:
        logger.error(f"Alert dispatch failed: {exc}")
        return {"dispatched": 0, "error": str(exc)}


def run_normalizer() -> str:
    """Run normalization engine between load and dbt."""
    from src.normalizer.runner import NormalizerRunner
    runner = NormalizerRunner()
    result = runner.run()
    return f"normalized {result['normalized']} jobs, {result['drift']} drift items, {result['errors']} errors"


def run_dbt(dbt_dir: str = "/app/dbt_transform") -> str:
    """Run dbt seed + run. fct_jobs_daily builds INCREMENTALLY (no drop, no
    gold-layer downtime, preserves snapshot history)."""
    logger = get_run_logger()
    # Note the `+` graph operator on both lines. fct_jobs_daily is incremental
    # and built with --full-refresh in its own pass, so it is excluded from the
    # bulk run. But models downstream of it (e.g. mart_skill_trend, which
    # ref()s fct_jobs_daily) must be excluded too — otherwise the bulk run tries
    # to build them before fct_jobs_daily exists and, on a fresh warehouse,
    # fails with `relation "dbt_dev_gold.fct_jobs_daily" does not exist`.
    # `fct_jobs_daily+` = fct_jobs_daily and all its descendants, so the second
    # pass builds the fact first and its dependents right after, in DAG order.
    # fct_jobs_daily runs INCREMENTAL (no --full-refresh). --full-refresh does
    # CREATE-TABLE-AS from the model, which is `select * from today_snapshot`
    # (current_date only): it both (a) DROPs the table mid-run, so Metabase
    # queries against the gold layer fail during every pipeline run, and (b)
    # WIPES all historical snapshots, keeping only today's. Incremental appends
    # today's snapshot into the existing table (guarded against same-day dupes),
    # so the table is never dropped, history is preserved, and it's much faster.
    cmds = [
        "dbt seed",
        "dbt run --exclude fct_jobs_daily+",
        "dbt run --select fct_jobs_daily+",
    ]
    for cmd in cmds:
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
