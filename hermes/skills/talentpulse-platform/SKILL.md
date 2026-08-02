---
name: talentpulse-platform
description: How the TalentPulse data platform is wired — the two Postgres databases, the daily pipeline schedule, which tables matter, and the hard safety rules. Read this before touching anything in the warehouse or the web box database.
license: MIT
metadata:
  hermes:
    tags: [TalentPulse, Postgres, dbt, Prefect, ETL, data platform]
---

# TalentPulse platform

A job-market ETL. Crawlers pull postings from four boards into object storage,
parsers turn them into JSON, a loader lands them in Postgres, dbt transforms
bronze → silver → gold → features, and a Next.js dashboard serves the result.

## The two databases

| | DSN env var | What lives there |
|---|---|---|
| Warehouse (this box) | `WAREHOUSE_DSN` | `raw.*` (crawl_log, job_detail, rejects), `dbt_dev_bronze/silver/gold/feature`, `normalization.*` |
| Web box (over tailnet) | `WEB_DSN` | `dbt_dev_gold/silver/feature` — a one-way daily COPY of the job tables, plus `app.*` (real user data) |

`psql "$WAREHOUSE_DSN" -c "..."` works directly. Both connect as role `hermes`,
not as admin.

## Hard rules

1. **Never touch `app.*` on the web box.** Real user accounts, CVs, chat history,
   job applications. The `hermes` role has no grant there and it must stay that
   way. The web box has no backup yet; the warehouse does (daily pg_dump to R2).
2. **The sync is one direction only: warehouse → web.** There is no reverse
   channel and there must not be one — both sides writing the same row means
   conflicts nobody resolves.
3. **Do not run `dbt run` or `dbt seed` unprompted.** dbt drops and recreates the
   gold tables; doing that mid-day makes the live site error out. Report what
   needs rebuilding and let a human trigger it.
4. **Never `--full-refresh` `fct_jobs_daily`.** It is incremental on purpose. A
   full refresh drops the table mid-run AND wipes every historical snapshot.
5. Read-only investigation is always safe and always preferred. Reach for
   `SELECT` first, every time.

## Daily schedule (cron is UTC, the product is UTC+7)

| VN time | What runs |
|---|---|
| 09:00 | VietnamWorks pipeline |
| 11:00 | ITviec |
| 12:00 | TopCV |
| 13:00 | LinkedIn |
| 14:00, 19:00 | alert dispatch |
| 15:00 | LLM skill extraction (re-runs dbt at the end) |
| 16:00 | sync warehouse → web box |
| 20:00 | warehouse backup to R2 |

Everything runs from GitHub Actions, not from a Prefect worker. The Prefect
server on this box only records runs and artifacts — the `.serve()` cron strings
inside the flow files are dead config and fire nothing.

## Key tables

- `raw.crawl_log` — work queue AND audit log. Status flows
  `pending → in_progress → success|failed|expired`. Rows stuck in `in_progress`
  mean a crawl died mid-flight; `requeue_stale()` sweeps them after 120 minutes
  on the next run.
- `raw.job_detail` — one row per `(source, source_job_id)`. That **pair** is the
  key; `source_job_id` alone is not unique across boards.
- `raw.job_detail_rejects` — quarantine holding the full payload, so a rule
  change can be replayed. Several rows per job accumulate over time, so count
  DISTINCT job ids, never raw rows.
- `dbt_dev_silver.silver_job_detail` — the business view everything reads.
- `normalization.job_normalization` — append-only, pruned to the newest 3 runs.

## The repo

`/opt/talentpulse/pipeline_data` — a dedicated clone. **Not** the GitHub Actions
runner checkout under `/home/github-runner/actions-runner/_work/…`:
`actions/checkout` wipes that directory on every CI run, and files created there
by root break the next checkout with EACCES. That exact failure happened on
2026-08-02. Work in the clone, push through CI, never deploy by hand.

The canonical git remote is `isolate` → `TalentPuse/TalentPulse_Pipeline_ETL-isolate`.
`origin` points at an older repo; pushing there is a mistake that has already
been made once.
