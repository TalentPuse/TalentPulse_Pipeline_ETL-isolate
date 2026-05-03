"""Standalone alert dispatch flow — runs on its own cron schedule.

Calls the dashboard API to dispatch job alerts to all active users.
Runs after all pipeline flows complete (VNW 2AM, ITviec 4AM, LinkedIn 6AM)
so fresh data is available. Also runs again at noon for mid-day coverage.
"""
from __future__ import annotations

import os
import time

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from orchestration.flows._shared import dispatch_dashboard_alerts, fmt_duration


@task(name="dispatch_alerts", retries=2, retry_delay_seconds=30, timeout_seconds=180)
def alert_dispatch() -> dict:
    return dispatch_dashboard_alerts()


@flow(name="alert-dispatch")
def alert_dispatch_flow() -> dict:
    """Dispatch job alerts to all active users via dashboard API."""
    t0 = time.time()
    result = alert_dispatch()
    dur = fmt_duration(time.time() - t0)
    dispatched = result.get("dispatched", 0)
    error = result.get("error")

    status = "success" if not error else f"error: {error}"
    create_markdown_artifact(
        markdown=(
            f"## Alert Dispatch\n"
            f"| Metric | Value |\n|--------|-------|\n"
            f"| Dispatched | {dispatched} |\n"
            f"| Status | {status} |\n"
            f"| Duration | {dur} |"
        ),
        key="alert-dispatch-result",
        description=f"Alert dispatch: {dispatched} jobs ({status})",
    )
    return result


if __name__ == "__main__":
    if os.getenv("PREFECT_DEPLOY", "0") == "1":
        alert_dispatch_flow.serve(
            name="alert-dispatch-daily",
            cron="0 7,12 * * *",
            tags=["alerts"],
        )
    else:
        alert_dispatch_flow()
