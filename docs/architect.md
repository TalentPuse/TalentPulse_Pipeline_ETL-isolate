# TalentPulse — System Architecture

**Last verified:** 2026-07-12, against the running stack — not from memory.
**Supersedes:** `ARCHITECTURE.md`, which still describes the 8-container / MinIO
layout retired on 2026-07-06.

---

## 0. The one thing to understand first

**The VPS does not run the pipeline.** It used to; it doesn't any more.

Crawling, parsing, dbt and LLM skill-extraction all run on **GitHub Actions
runners** — ephemeral machines GitHub gives you for free — which reach back into the
VPS over Tailscale to write their results. The VPS only hosts what must be *always
on*: the database, the Prefect UI, Metabase, and the website.

If you remember one thing from this document, remember that. Almost every "why is it
built this way" question resolves to it.

```
  GitHub Actions runner (ephemeral, free, 7GB RAM)
  ┌────────────────────────────────────────────┐
  │  docker run ghcr.io/…-worker               │
  │    crawl → parse → load → dbt → LLM        │
  └───────────────────┬────────────────────────┘
                      │ Tailscale (WireGuard)
                      ▼
              VPS: Postgres, Prefect, Metabase
              (always-on only — no compute)
```

---

## 1. Repositories

Two git repos, deployed independently.

| Repo | Path | Remote | Contains |
|---|---|---|---|
| **pipeline_data** | `D:\TalentPulse\pipeline_data` | `origin` → `TalentPuse/TalentPulse_Pipeline_ETL` (`develop`) | Crawlers, parsers, loaders, normalizer, LLM skill extractor, dbt project, Prefect flows, warehouse compose, backup |
| **talentpulse** | `D:\TalentPulse\talentpulse` | *(none yet)* | Monorepo: `apps/backend` (FastAPI), `apps/frontend` (Next.js 14), `apps/mcp` (FastMCP) |

`talentpulse` is a monorepo with **one** compose file and **three** deploy workflows
(`backend.yml`, `frontend.yml`, `mcp.yml`), each bringing up only its own service.

---

## 2. Runtime topology

### Target — two boxes

```
                     ┌───────────────────── GitHub Actions ─────────────────────┐
                     │  6 scheduled workflows, ephemeral runners, 0 VPS RAM     │
                     │                                                          │
                     │  pipeline-vnw        09:00  crawl VietnamWorks           │
                     │  pipeline-itviec     11:00  crawl ITviec (Playwright)    │
                     │  pipeline-linkedin   13:00  crawl LinkedIn               │
                     │  pipeline-alerts     14:00 + 19:00                       │
                     │  pipeline-skill-…    15:00  LLM extraction (OpenRouter)  │
                     │  pipeline-backup     20:00  pg_dump + parquet → R2       │
                     └────────┬───────────────────────────────────┬─────────────┘
                              │ Tailscale                         │ Tailscale
                              │                                   │
  ┌───────────────────────────▼─────────┐      ┌──────────────────▼──────────────┐
  │  WEB BOX          8 GB / 4 vCPU     │      │  WAREHOUSE BOX          4 GB    │
  │                                     │      │                                 │
  │  tp-frontend  :8002   Next.js       │      │  postgres      :5432            │
  │  tp-backend   :8001   FastAPI       │      │    warehouse DB                 │
  │  tp-mcp       :8080   FastMCP       │      │      raw → bronze → silver      │
  │  tp-latex             CV → PDF      │      │          → gold → feature       │
  │                                     │      │                                 │
  │  postgres     :5432   (loopback)    │◀─────│  prefect-server :4200  history  │
  │    talentpulse DB                   │ sync │  metabase       :3000  internal │
  │      app, user_alerts, public  (RW) │ daily│                        BI       │
  │      dbt_dev_*   read-only copy (RO)│ 1-way└─────────────────────────────────┘
  └─────────────────────────────────────┘
                    ▲
                    │ HTTPS
                end users
```

**The backend reaches its database over a loopback socket.** Nothing in the request
path crosses the network. The only cross-box traffic is the daily sync — a few MB, at
15:00, with nobody waiting on it.

### Where it actually is today

Both stacks still share one host, and user data still lives inside the `warehouse`
database. `website-warehouse-db-split.md` has the migration; §6 explains why that is
urgent rather than cosmetic.

---

## 3. Data flow

```
  VietnamWorks   ITviec (Playwright)   LinkedIn
        │              │                  │
        └──────────────┴──────────────────┘
                       │  raw HTML
                       ▼
              Cloudflare R2   (talentpulse-raw)
                       │
                       ▼  parse
              raw.job_detail            ← the durable landing zone
              raw.crawl_log
              raw.job_detail_rejects
                       │
                       ▼  normalize    (rules live in SQL, not code —
              normalization.*             editable at runtime, no deploy)
                       │
                       ▼  dbt (14 models)
        ┌──────────────────────────────────────────┐
        │ bronze   stg_job_detail            VIEW  │
        │ silver   silver_job_detail         VIEW  │
        │          silver_skill_long         VIEW  │
        │          silver_company_dim        VIEW  │
        │ gold     fct_jobs_daily     TABLE (incr) │ one row per
        │          mart_skill_demand         TABLE │ (job × snapshot_date)
        │          mart_company_hiring       TABLE │
        │          mart_salary_by_level      TABLE │
        │ feature  job_features              TABLE │
        └──────────────────────────────────────────┘
                       │
                       ├──▶ Metabase        (internal BI, reads warehouse direct)
                       ├──▶ sync → web DB → backend → frontend  (public analytics)
                       └──▶ MCP server → the backend's AI agents
                       │
                       ▼  LLM (OpenRouter)
              raw.skill_extraction_log
                       │
                       ▼  alert dispatch  (HTTP, not SQL)
              POST tp-backend:8001 → Telegram + Resend email
```

**Bronze and silver are VIEWS, not tables.** They hold no storage; every query
against them re-executes the underlying SQL over `raw.job_detail`. This matters — see
§6.

---

## 4. Databases: who owns what

| Schema | Owner | Re-creatable? | Notes |
|---|---|---|---|
| `raw` | pipeline | yes — re-crawl | 13 MB, the largest |
| `normalization` | pipeline | yes — re-seed | rules, editable at runtime |
| `dbt_dev_bronze`, `_silver` | dbt | yes | **VIEWs** |
| `dbt_dev_gold`, `_feature` | dbt | yes | TABLEs, 368 kB |
| `app` | **backend** | **NO** | users, CVs, job_applications, chat, interviews |
| `user_alerts` | **backend** | **NO** | subscribers, subscriptions, alert_log |
| `public` | **backend** | **NO** | `alembic_version`, LangGraph `checkpoint*` |

Total database: **26 MB**. Of that, **1.8 MB is irreplaceable**; the rest is a cache
of the internet.

---

## 5. Scheduling

Cron lives in **GitHub Actions, not Prefect**. Prefect records runs and gives you a
UI; it does not fire them. Firing them would need a `prefect-worker` container on the
VPS — deliberately removed to save RAM.

| Time (VN) | Workflow | Runs on |
|---|---|---|
| 09:00 | VietnamWorks crawl | GHA runner |
| 11:00 | ITviec crawl (Playwright) | GHA runner |
| 13:00 | LinkedIn crawl | GHA runner |
| 14:00, 19:00 | Alert dispatch | GHA runner |
| 15:00 | LLM skill extraction | GHA runner |
| 20:00 | Backup → R2 | GHA runner |
| push to `develop` | Build images, deploy compose | GHA + self-hosted runner on the warehouse box |

Crons are **UTC** in the workflow files; the table shows the VN equivalent.

---

## 6. Constraints that will bite you

Each of these has already caused, or will cause, a real failure.

**Silver is a view over `raw`, and the backend queries it directly** — 4 places
(`jobs.py:91,93,180`, `admin.py:405`). Every page load that touches silver
**re-derives it from `raw.job_detail` on the fly.** At 721 jobs that is invisible. At
200k jobs it is a full scan per request. Materialising silver into the web DB — which
the sync must do anyway, since a view has no storage to copy — fixes this as a side
effect.

**Postgres cannot JOIN across databases.** The backend JOINs `app.job_applications`
against `dbt_dev_gold.fct_jobs_daily` inside single SQL statements. That is *why* the
web DB must hold a copy of the job tables. It is a constraint, not a preference.

**Restoring the warehouse currently destroys user data.** `app`, `user_alerts` and
`public` live inside the `warehouse` database, and `pg_restore` works per-database —
so recovering from a bad dbt run rolls back user signups, CV uploads and job
applications. This is the most urgent item in this document.

**The backend runs ONE uvicorn worker.** One process, one core. AI streaming is
I/O-bound and fine on it, but **CV parsing (PyMuPDF) and LaTeX rendering are
CPU-bound and block the event loop** — one user uploading a heavy CV stalls everyone,
including people mid-chat. This is the real ceiling at a few hundred users. Not RAM.

**Prefect 2 OSS has no authentication.** Port 4200 must never face the internet. It
is bound to `127.0.0.1` and reached over the tailnet.

**dbt drops and recreates its tables every run.** This rules out Postgres logical
replication for the sync (a publication tracks tables by identity) and is why the
sync is a plain truncate-and-load.

**Metabase's JVM ignores the container memory limit.** Without an explicit `-Xmx` it
sizes its heap from the *host's* total RAM and gets OOM-killed on a small box. Capped
at 768 MB.

---

## 7. Backup and recovery

Daily at 20:00, to Cloudflare R2 (`talentpulse-backup`):

- **`db/<date>/warehouse-<ts>.dump`** — `pg_dump -Fc`. This is *the* backup: one
  `pg_restore` rebuilds the database exactly. Verified end to end — 26 MB database →
  4.43 MB dump → restored into a scratch DB with matching row counts.
- **`parquet/<date>/*.parquet`** — zstd Parquet of the gold/feature marts, 106 kB
  total. Queryable straight off R2 with DuckDB. **This is not a backup**: it stores
  column values and no schema. Do not plan a recovery around it.

Retention is grandfather-father-son — 14 daily → 8 weekly → 12 monthly — because the
failure that matters is corruption noticed *late*, and a flat "delete after 30 days"
leaves you nothing once the bad data is 31 days old. Two years of daily runs keeps 31
files / 141 MB: 1.4 % of R2's free tier. A floor of 7 stops the pruner ever emptying
the bucket.

After the DB split this becomes **two** dumps — and the point of the exercise is that
restoring the warehouse then touches zero user rows.

---

## 8. Network and secrets

- **Tailscale is the only path** between the boxes and from CI. Postgres (5432) and
  Prefect (4200) are bound to `127.0.0.1` and bridged with `tailscale serve` — a
  loopback docker publish is *not* reachable over the tailnet on its own.
- Public surface: Metabase 3000 (warehouse box); the website behind a reverse proxy
  (web box).
- **Two** GitHub Variables, not one: `WAREHOUSE_TAILNET_IP` and `WEB_TAILNET_IP`. A
  single `VPS_TAILNET_IP` was what made the config unable to express two machines
  even once you had them.
- **Secrets fail fast.** `config.py` refuses to boot on a missing, too-short, or
  placeholder value — because `os.getenv(name, "a-default")` is exactly how
  production ends up signing JWTs with a string that is committed to the repo.

---

## 9. See also

- `gha-migration-runbook.md` — moving the pipeline onto GitHub Actions; R2,
  Tailscale and GitHub Secrets setup.
- `website-warehouse-db-split.md` — splitting user data out of the warehouse.
- `ARCHITECTURE.md` — **stale.** Describes the retired 8-container layout.
