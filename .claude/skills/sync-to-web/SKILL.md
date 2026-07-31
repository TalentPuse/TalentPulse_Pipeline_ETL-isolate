---
name: sync-to-web
description: The warehouse-to-web-box table sync (orchestration/flows/sync_to_web.py) and its known failure modes — column types, missing objects, views without storage, staging swap. Use when editing that flow, when adding a table the website must read, or when the website shows empty/broken job data after a pipeline run.
---

# Warehouse → web sync

`orchestration/flows/sync_to_web.py`. One direction only, once a day.

Scheduled by `.github/workflows/pipeline-sync-to-web.yml`, cron `0 9 * * *` UTC =
**16:00 VN** — after skill-extraction (15:00, which re-runs dbt as its last step)
so it publishes a finished warehouse, and before backup (20:00). The `.serve()`
branch in the flow's `__main__` mirrors that cron but is not what fires in
production; see the `prefect-flows` skill. Change one cron and you must change
the other.

Needs `WEB_DATABASE_URL` (a full DSN secret for the web box, not assembled from
`WEB_TAILNET_IP` + the warehouse `DB_*`). It is forwarded through the `-e` list
in `.github/actions/run-flow/action.yml` — a new env var missing from that list
reaches the container as nothing at all.

## Why it exists

The dashboard backend JOINs `app.job_applications` against
`dbt_dev_gold.fct_jobs_daily` **in a single SQL statement**, and Postgres cannot
join across databases. Once app data moved to its own DB on the web box, that box
is forced to hold a read-only copy of the job tables. Constraint, not convenience.

Logical replication was rejected: dbt DROPs and recreates tables every run, which
breaks a publication (it tracks tables by identity) and requires manual re-sync —
and views cannot be published at all, which kills the two silver objects outright.

## Non-negotiables

**One direction.** Warehouse → web. There is no reverse channel and there must not
be one: two-way sync means both sides write the same row with no conflict resolution.

**Never default `WEB_DATABASE_URL`.** `_web_dsn()` raises when it is unset. A default
pointing back at the warehouse would make the job overwrite its own source.

**Keep the `dbt_dev_*` schema names on the web side.** Collapsing them into one
`analytics` schema means editing the schema prefix in 7 hand-written SQL files for
zero functional gain.

## The failure modes, all of which have actually happened

| Symptom | Cause | Fix in place |
|---|---|---|
| `can't adapt type 'dict'` on `silver_job_detail` | psycopg2 parses jsonb → dict on read, cannot adapt it on write | `register_default_json/jsonb(loads=lambda x: x)` at module import — read JSON as raw strings |
| Sync reports "3019 rows OK" but the app throws `argument of AND must be type boolean, not type text` | target table created with every column as `text`, so `fct_jobs_daily.is_active` became text | column types are read from the source's `pg_attribute` via `format_type(atttypid, atttypmod)` — **ask Postgres, never guess** |
| Whole flow aborts, nothing syncs | `silver_skill_long` not built yet → `UndefinedTable` | catch `psycopg2.errors.UndefinedTable` per object, log loudly, keep yesterday's data, continue with the rest |
| Website shows an empty table, silently | a source object legitimately returned 0 rows | the flow collects `empty` and `missing` lists and `logger.error`s them into the artifact |

## How one object moves

1. `SELECT *` from `<schema>.<name>` on the warehouse; capture rows, column names,
   and real column types.
2. On the web box: `CREATE SCHEMA IF NOT EXISTS`, drop any stale
   `<name>__staging`, then create staging as `LIKE <name> INCLUDING DEFAULTS` when
   the target already exists, otherwise from the captured types.
3. `execute_values` INSERT into staging, `page_size=1000`.
4. `DROP TABLE ... CASCADE` + `ALTER TABLE <staging> RENAME TO <name>` — both in
   the **same transaction**, so readers see the old table right up to the swap and
   a failed sync leaves yesterday's data rather than an empty table.

`is_view` in `OBJECTS` does not change how the table is created (both cases become
a real table on the web side). It is there to mark objects that have **no storage**
on the warehouse — the detail that is easiest to forget when adding one.

## Caveat when changing column types

Because step 2 prefers `LIKE <existing target>`, a wrong type that once landed on
the web box **persists across every future sync**. Changing a column's type in dbt
is not enough — drop the table on the web box so the next run recreates it from
the source types.

## Adding an object

Append to `OBJECTS` as `(schema, name, is_view)`, and make sure dbt actually builds
it before 15:00 VN. If it is a view, set `is_view=True`.
