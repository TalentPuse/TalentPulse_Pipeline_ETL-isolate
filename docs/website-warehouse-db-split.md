# Splitting the website database from the warehouse — design

**Status:** proposal. Nothing here is implemented yet.
**Builds on:** `gha-migration-runbook.md` (the two-VPS split).

---

## 1. The actual problem

User data lives *inside* the warehouse database. Not beside it — inside it.

```
warehouse (one database)
├── raw, dbt_dev_bronze/silver/gold/feature   crawled + derived   (re-creatable)
├── app          users, cv_documents, job_applications,
│                 chat_*, interview_*, telegram_connections       (irreplaceable)
├── user_alerts  subscribers, subscriptions, alert_log            (irreplaceable)
└── public       alembic_version, LangGraph checkpoint*           (backend state)
```

Two kinds of data with completely different lifecycles are welded together:

| | Crawl / dbt data | User data |
|---|---|---|
| If lost | re-crawl, re-run dbt | **gone forever** |
| Rewritten | every day, by dbt | never — only appended to |
| Size | 13 MB, growing | 1.8 MB |

**The concrete failure this causes:** the daily backup dumps the whole `warehouse`
database. Restoring it — to recover from a bad dbt run, a botched migration, a
corrupted table — also rolls back every user who signed up, every CV uploaded and
every job application recorded since that dump was taken. *The recovery procedure
for a crawl problem destroys user data.* No amount of care in the restore runbook
fixes that; the two simply must not share a database.

The tailnet round-trip from the website to the warehouse is a secondary concern.
The primary one is the shared blast radius.

**The cut is clean:** no dbt model reads `app` or `user_alerts` (verified by grep
over `dbt_transform/models/`). The dependency runs one way only.

---

## 2. Target

```
   WEB BOX (4GB)                          WAREHOUSE BOX (4GB)
   ┌────────────────────────┐             ┌──────────────────────────┐
   │ tp-backend      :8001  │             │ postgres        :5432    │
   │ tp-frontend     :8002  │             │   warehouse DB:          │
   │ tp-mcp          :8080  │             │     raw, dbt_dev_*       │
   │ tp-latex               │             │ prefect-server  :4200    │
   │                        │             │ metabase        :3000    │
   │ postgres        :5432  │             └──────────────────────────┘
   │   talentpulse DB:      │                          │
   │     app         (RW)   │◀────── sync jobs ────────┘
   │     user_alerts (RW)   │        one way, daily
   │     public      (RW)   │
   │     dbt_dev_*   (RO)   │
   └────────────────────────┘
```

- **Web DB owns user data.** The backend reads and writes it over a loopback
  socket, as it does today — login, CV upload and chat stop traversing WireGuard.
- **Warehouse DB owns job-market data.** dbt owns it outright.
- **Sync is one-way, warehouse → web.** Nothing on the warehouse box ever reads
  user data; alert dispatch already goes through the backend's HTTP API.

The website then survives a warehouse outage: users log in, browse jobs, chat and
review applications against the last synced snapshot.

---

## 3. The web DB must hold a copy of the job tables — this is not optional

The backend does **cross-schema JOINs inside a single SQL statement**:

- `app/services/application_service.py:14` — `app.job_applications` ⋈ `dbt_dev_gold.fct_jobs_daily`
- `app/services/admin.py:363, 418, 453` — same shape
- `app/api/jobs.py:91, 93, 180` and `app/api/admin.py:405` — ⋈ `dbt_dev_silver.*`

Postgres cannot join across two servers. So either the web DB keeps a local copy of
the job tables, or all of those queries get rewritten into two round trips plus an
application-side join — which is strictly worse: more code, more latency, and it
throws the query planner away.

Copy the job data to the web box. It is small.

### What has to be synced

| Object | Type in warehouse | Size | Note |
|---|---|---|---|
| `dbt_dev_gold.*` (4) | TABLE | 264 kB | |
| `dbt_dev_feature.*` (1) | TABLE | 104 kB | |
| `dbt_dev_silver.silver_job_detail` | **VIEW** | — | must be **materialised** on write |
| `dbt_dev_silver.silver_skill_long` | **VIEW** | — | same |

**The silver objects are views, not tables** — there is no storage to copy. The sync
job has to `SELECT *` from the view on the warehouse and insert the rows into a real
table on the web box. Easy to miss; it would break the first run.

Total payload: a few MB.

**Keep the `dbt_dev_*` schema names on the web box.** Landing everything in a tidy
`analytics` schema would mean rewriting the schema prefix in 7 files of hand-written
SQL for zero functional gain — and every one of those edits is a chance to introduce
a bug. Ugly names beat a risky rename.

---

## 4. Why not logical replication

It looks like the obvious answer and it does not work here: **dbt DROPs and
recreates its tables on every run.** A publication tracks tables by identity, so
when dbt drops `fct_jobs_daily` and builds a new one the subscription breaks and
needs a manual re-sync. Views cannot be published at all, which kills the silver
tables outright.

A boring "load into staging, swap in one transaction" job survives dbt's
drop/recreate, handles views, and moves 3 MB in under a second. Use that.

---

## 5. Sync job

A Prefect flow — `orchestration/flows/sync_to_web.py` — scheduled **after
skill-extraction** (VN 15:00) so it publishes a finished warehouse:

1. Connect to warehouse (read) and web DB (write), both over the tailnet.
2. For each object: `SELECT *` from the warehouse → load into a staging table on web.
3. Swap staging into place **inside one transaction**, so a reader never sees a
   half-loaded table and a failed sync leaves yesterday's data intact rather than an
   empty one.
4. Record row counts as a Prefect artifact, so a silently-empty sync is visible.

The failure mode to design for: a failed sync must be **loud but harmless**. Stale
job data for a day is fine. An empty `fct_jobs_daily` on the live website is not.

---

## 6. Backup, after the split

The daily backup becomes two — and this is the whole point of the exercise:

| | What | Retention | Why |
|---|---|---|---|
| **web** | `pg_dump` of app + user_alerts + public | GFS, as today | irreplaceable, ~2 MB |
| **warehouse** | `pg_dump` of the warehouse DB | can be shorter | re-creatable by re-crawling |

Restoring the warehouse then touches zero user rows. That is the outcome being
bought here.

The Parquet export stays on the warehouse side — it archives the dbt marts and has
nothing to do with user data.

---

## 7. Migration steps

Order matters; step 7 is the only irreversible one.

1. **Stand up Postgres on the web box.** Add it to the web compose file, ~800 MB
   limit. The box has room: backend 600 + frontend 400 + mcp 150 + latex 150 + pg
   800 + host 700 ≈ **2.8 GB of 4 GB**.
2. **Dump the user schemas from the warehouse:**
   ```
   pg_dump -Fc -n app -n user_alerts -n public warehouse > user-data.dump
   ```
   `public` is included deliberately: it holds `alembic_version` (the backend's
   migration pointer) and the LangGraph `checkpoint*` tables (AI agent state).
   Leaving it behind makes the backend replay every migration from scratch against
   an empty database.
3. **Restore into the web DB, then verify row counts** — 58 users, 3
   job_applications, 1344 alert_logs, 15 interview_sessions, 32 chat_rooms. Do not
   proceed on a mismatch. Use real `count(*)`: several of these tables have never
   been ANALYZEd, so `reltuples` reports `-1` and cannot be trusted.
4. **Build and run the sync flow once**, so the `dbt_dev_*` tables exist on the web
   box before anything queries them.
5. **Point the backend's `DATABASE_URL` at the web DB** and deploy.
6. **Exercise the app end to end**: log in as an existing user, open the job board,
   open an application. Those three paths touch all four schemas.
7. **Only then drop `app`, `user_alerts` and `public` from the warehouse** — and
   only after taking a fresh warehouse backup immediately before the drop.

Steps 1–6 are additive and reversible: until step 7 the old data is still in the
warehouse and `DATABASE_URL` can be pointed back.

---

## 8. Risks

- **Dual-write window (steps 5–7).** The warehouse copy of `app` is stale but still
  present. Anything still pointing at it writes into a grave. Grep every
  `DATABASE_URL` consumer before flipping — note the MCP server has its *own*
  (`MCP_DATABASE_URL`) and correctly reads the warehouse; it must **not** be
  repointed.
- **Backups must land in the same change, not "after".** If the web DB has no
  backup on day one, the split makes things strictly *worse*: user data would then
  live in the one database nobody dumps.
- **Stale analytics becomes explicit.** The site will show job data as of the last
  sync. That is already true in practice for a daily-crawl product, but it stops
  being invisible — show "data as of <date>" in the UI rather than letting it be a
  silent lie.
- **Metabase loses sight of user tables.** It reads the warehouse. If any dashboard
  joins users to jobs, it breaks at step 7 — check before dropping.

---

## 9. Open questions

- Does anything other than the backend write to `app` / `user_alerts`? The alert
  dispatch path goes through the backend's HTTP API, which suggests not — confirm
  before step 7.
- Should the warehouse keep a *read-only* copy of aggregate user counts for
  Metabase dashboards? Cheap to add later; do not block the split on it.
