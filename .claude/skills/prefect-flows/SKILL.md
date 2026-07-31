---
name: prefect-flows
description: Conventions for orchestration/flows/* — Prefect task/flow structure, retry and timeout budgets, the run-once vs .serve() split, and markdown artifacts. Use when adding a pipeline stage, editing any *_pipeline.py, changing a task timeout or retry count, or debugging a flow that dies before finishing.
---

# Prefect flows (`orchestration/flows/`)

Flows are written with Prefect 2.16 decorators but in production they run
**one-shot from GitHub Actions**, not from a Prefect worker. There is a Prefect
server on the warehouse box purely for run visibility and artifacts.

## The run-once / deploy split

Every flow ends with:

```python
if __name__ == "__main__":
    if os.getenv("PREFECT_DEPLOY", "0") == "1":
        my_flow.serve(name=..., cron=...)   # long-lived deployment
    else:
        my_flow()                            # run once and exit
```

`.github/actions/run-flow/action.yml` deliberately does **not** forward
`PREFECT_DEPLOY`. If you add it to the `docker run -e` list, every scheduled GHA
job turns into a process that registers a deployment and then blocks forever.
The cron strings inside `.serve()` are effectively dead config — the real
schedules are the `cron:` lines in `.github/workflows/pipeline-*.yml`.

## Standard stage order

```
listing_crawl -> seed_queue -> detail_crawl -> detail_parse -> load_warehouse
              -> normalize -> dbt_transform -> dispatch_alerts
```

`vnw`, `itviec`, `linkedin`, `topcv` all follow it. Shared helpers live in
`_shared.py`: `run_dbt`, `run_normalizer`, `dispatch_dashboard_alerts`,
`counters_table`, `fmt_duration`. Put anything reused by two flows there instead
of copying it.

## Timeouts and retries are a budget, not decoration

The chain is: GHA `timeout-minutes` > flow wall time > task `timeout_seconds` >
the crawler's own internal deadline. Violating that order means the job is killed
mid-crawl and **nothing downstream runs** — no parse, no load, no alerts. That
happened on 2026-07-23 with LinkedIn.

Current values worth knowing:

| Task | Setting | Why |
|---|---|---|
| `vnw detail_crawl` | `retries=2`, `timeout_seconds=7200` | httpx, fast |
| `itviec detail_crawl` | **`retries=0`**, `timeout_seconds=7200` | a retry restarts the whole browser loop and produces a zombie double-loop; do not raise it |
| `linkedin detail_crawl` | `retries=1`, `timeout_seconds=1800` | backstop only — the crawler stops itself at `config.LINKEDIN_DETAIL_MAX_SECONDS` (default 1200s). Keep this above that budget and below the workflow timeout. |
| `dbt_transform` / `normalize` | `timeout_seconds=3600` | |
| `dispatch_alerts` | `timeout_seconds=180` | just an HTTP POST |

A retry on a task that claims rows from `raw.crawl_log` is only safe because
`requeue_stale()` runs at the start of each detail crawl. Keep that call when
adding a new source.

## Every stage emits an artifact

```python
create_markdown_artifact(
    markdown=counters_table(source, stage, counters, duration),
    key="<source>-<stage>",
    description="...",
)
```

`key` must be stable — Prefect keys artifacts by it and shows the latest. The
flow itself emits a `<source>-pipeline-summary` table at the end. Counter dicts
returned by tasks feed both the artifact and the summary, so keep returning
plain dicts of ints.

## Adding a stage

- [ ] `@task(name=..., retries=..., timeout_seconds=...)`, returns a counter dict.
- [ ] Time it with `t0 = time.time()` and emit a `counters_table` artifact.
- [ ] Wire it into the flow body in DAG order and add a row to the summary table.
- [ ] Prefect tasks here are called sequentially and the data dependency is
      implicit — if the new stage must run after another, call it after, do not
      rely on `.submit()`/futures.
- [ ] If it needs new env vars, add them to the `docker run -e` list in
      `.github/actions/run-flow/action.yml` **and** to the calling workflow's
      `env:` block. Missing either one means the container silently gets nothing.
