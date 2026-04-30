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


def dispatch_dashboard_alerts() -> dict:
    """Call dashboard API to dispatch job alerts after fresh data is loaded."""
    logger = get_run_logger()
    url = os.getenv("DASHBOARD_API_URL", "http://tp-backend:8001")
    secret = os.getenv("ALERT_DISPATCH_SECRET", "")
    try:
        resp = requests.post(
            f"{url}/api/admin/alerts/dispatch-internal",
            headers={"X-Webhook-Secret": secret},
            timeout=120,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"Dashboard alerts dispatched: {result}")
        return result
    except Exception as exc:
        logger.warning(f"Alert dispatch failed (non-fatal): {exc}")
        return {"dispatched": 0, "error": str(exc)}


def run_dbt(dbt_dir: str = "/app/dbt_transform") -> str:
    """Run dbt seed + run, with full-refresh for fct_jobs_daily."""
    logger = get_run_logger()
    cmds = [
        "dbt seed",
        "dbt run --exclude fct_jobs_daily",
        "dbt run --select fct_jobs_daily --full-refresh",
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
