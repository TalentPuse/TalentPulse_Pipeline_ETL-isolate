---
name: dbt-warehouse
description: Conventions and landmines for the dbt_transform warehouse (bronze/silver/gold/features). Use when adding or editing any dbt model, seed, or test, when changing how dbt is invoked, or when a gold table shows up empty/stale. Covers the fct_jobs_daily incremental rule, the two-pass run order, and the dbt_dev_* schema naming.
---

# dbt warehouse (`dbt_transform/`)

Postgres, single `dev` target, schemas prefixed `dbt_dev_*`. Layer config lives in
`dbt_transform/dbt_project.yml`:

| Layer | Materialization | Schema | Contents |
|---|---|---|---|
| bronze | view | `dbt_dev_bronze` | `stg_job_detail` — type-cast + `is_active_calc`/`is_expired_calc` |
| silver | view | `dbt_dev_silver` | `silver_job_detail`, `silver_skill_long`, `silver_skill_enriched`, `silver_skill_unified`, `silver_company_dim` |
| gold | **table** | `dbt_dev_gold` | `fct_jobs_daily`, `mart_*` |
| features | **table** | `dbt_dev_feature` | `job_features` (ML feature store, one row per job) |

Source of everything: `raw.job_detail` (declared in `models/sources.yml`). Note the
name — some older docs say `job_detail_v2`, that table does not exist.

## The two rules you must not break

**1. `fct_jobs_daily` is incremental. Never `--full-refresh` it.**
It is `materialized='incremental'`, `incremental_strategy='delete+insert'`,
`unique_key=['source','source_job_id','snapshot_date']`. A full refresh does
CREATE-TABLE-AS from `select * from today_snapshot` (current_date only), which
(a) DROPs the table mid-run so Metabase and the dashboard error out during every
pipeline run, and (b) wipes all historical snapshots. Both have happened.

Also do **not** re-add a `where current_date not in (select snapshot_date ...)`
guard — it freezes the gold layer at the size of the day's first dbt run, and
every later run that day inserts 0 rows. The comment at the top of
`models/gold/fct_jobs_daily.sql` records this; keep it there.

**2. dbt is invoked in two passes, in this order** (`orchestration/flows/_shared.py::run_dbt`):

```
dbt seed
dbt run --exclude fct_jobs_daily+          # everything else
dbt run --select fct_jobs_daily+           # the fact AND its descendants
```

The `+` graph operator matters on **both** lines. Models downstream of
`fct_jobs_daily` (e.g. `mart_skill_trend`) must be excluded from pass 2's
exclusion too, or on a fresh warehouse the bulk run fails with
`relation "dbt_dev_gold.fct_jobs_daily" does not exist`.

If you add a model that `ref()`s `fct_jobs_daily`, you get the correct behaviour
for free — do not add it to an explicit list.

## Seeds

`dbt_transform/seeds/`: `city_map`, `company_size_map`, `degree_map`, `fx_rates`,
`job_level_map`, `job_title_category_map`, `salary_period_map`. These are lookup
tables that silver joins against. Changing an FX rate or adding a city alias is a
seed edit, not a model edit. `job_title_category_map.priority` is pinned to
`integer` in `dbt_project.yml` — keep it.

## Category and level resolution (silver_job_detail)

Order of precedence, all in one model:
1. `vnw_category` — hardcoded VietnamWorks `job_function` mappings
2. `job_title_category_map` seed, lowest `priority` wins
3. `normalization.job_normalization` (written by `src/normalizer/runner.py`, joined
   as `norm`, `distinct on (source, source_job_id) ... order by run_at desc`)
4. literal `'Other'`

Job level falls back title-map → `'Mid-level'`. Salary is normalized to VND-monthly
via `fx_rates` × `salary_period_map.months_multiplier`, with a sanity override:
`salary_period_id = 2` (Hourly) but `salary_min > 10_000_000` is treated as Monthly.

## Tests exist but are not run

There are ~50 tests across `models/**/_*__tests.yml`. **`run_dbt()` never calls
`dbt test`.** If you are changing a model, run it yourself:

```
dbt test --project-dir dbt_transform --profiles-dir dbt_transform --select <model>
```

`profiles.yml` defaults every connection var (`DB_HOST` → localhost etc.), so
`dbt parse` and `dbt compile` work with no environment at all. `dbt run`/`dbt test`
need real `DB_*` values pointing at the warehouse over the tailnet.

## Adding a model — checklist

- [ ] Put it in the right layer directory; materialization comes from `dbt_project.yml`, do not re-declare it unless you deviate.
- [ ] `ref()` upstream models, `source()` only for `raw.*`.
- [ ] Add tests to the layer's `_*__tests.yml`.
- [ ] If it must be visible on the website, add it to `OBJECTS` in `orchestration/flows/sync_to_web.py` — see the `sync-to-web` skill.
- [ ] `dbt parse` at minimum; `dbt build --select <model>` if you have DB access.
