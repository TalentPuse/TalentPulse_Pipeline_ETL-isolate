"""Shared helpers for Prefect pipeline flows."""
from __future__ import annotations

import subprocess

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
