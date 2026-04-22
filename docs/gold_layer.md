# Gold Layer

Silver + Gold layer xây bằng dbt. Bronze (raw view) → Silver (normalized views) → Gold (materialized marts).

## Architecture

```
raw.job_detail (loader output)
        │
        ▼
  bronze/stg_job_detail (view)        ← filter is_active
        │
        ▼
┌──────────── SILVER (3 views) ────────────┐
│ silver_job_detail   ── salary→VND/month, │
│                       city/degree/size   │
│                       canonicalized      │
│ silver_skill_long   ── 1 row per         │
│                       (job, skill)       │
│ silver_company_dim  ── deduped company   │
└──────────────────────────────────────────┘
        │
        ▼
┌────────────── GOLD (4 tables) ───────────┐
│ fct_jobs_daily       (incremental)       │
│ mart_skill_demand    (table)             │
│ mart_company_hiring  (table)             │
│ mart_salary_by_level (table)             │
└──────────────────────────────────────────┘
        │
        ▼
   Metabase (port 3000) reads gold.*
```

## Schemas trong Postgres

| Schema | Content |
|---|---|
| `raw` | Loader output (job_detail, crawl_log, job_detail_rejects) |
| `dbt_dev_seeds` | Lookup tables (fx_rates, city_map, degree_map, ...) |
| `dbt_dev_bronze` | Pass-through views |
| `dbt_dev_silver` | Normalized views |
| `dbt_dev_gold` | Materialized marts (table) |

## Run

```bash
cd dbt_transform
DBT_PROFILES_DIR=. dbt deps          # install dbt_utils (one-off)
DBT_PROFILES_DIR=. dbt seed          # load 5 lookup CSVs
DBT_PROFILES_DIR=. dbt build         # silver views + gold tables + tests
```

Idempotent: `fct_jobs_daily` không double khi re-run cùng ngày.

## Seeds

| File | Rows | Purpose |
|---|---|---|
| `fx_rates.csv` | 3 | VND=1, USD=25000, JPY=165 |
| `salary_period_map.csv` | 4 | id → months_multiplier (Monthly=1, Hourly=176, Yearly=0.083) |
| `city_map.csv` | 10 | Map raw city name → canonical + region |
| `degree_map.csv` | 9 | id → label (Bachelor, Master, ...) |
| `company_size_map.csv` | 11 | id → label + bucket (Micro/Small/Medium/Large/Enterprise) |

Update tay khi thấy raw value mới (vd thêm city, currency).

## Gold marts — Metabase queries

### 1. Top skills demand
```sql
select skill, n_jobs, pct_of_jobs, avg_salary_vnd
from dbt_dev_gold.mart_skill_demand
order by n_jobs desc
limit 15;
```
Bar chart, X=skill, Y=n_jobs.

### 2. Salary by level × city
```sql
select job_level, city_canonical, p25_vnd, p50_vnd, p75_vnd, n_visible_jobs
from dbt_dev_gold.mart_salary_by_level
order by p50_vnd desc;
```
Bar chart with error bars (p25 → p75), grouped by city.

### 3. Top hiring companies
```sql
select company_name, n_jobs, primary_city, avg_views, avg_salary_vnd
from dbt_dev_gold.mart_company_hiring
order by n_jobs desc, avg_views desc
limit 20;
```
Table.

### 4. Skills with highest paying jobs
```sql
select skill, n_jobs, avg_salary_vnd, avg_salary_senior_vnd
from dbt_dev_gold.mart_skill_demand
where avg_salary_vnd is not null
order by avg_salary_vnd desc
limit 10;
```
Bar chart, secondary metric for comparison.

### 5. Time series (after multi-day snapshots)
```sql
select snapshot_date, count(*) as active_jobs,
       round(avg(salary_vnd_monthly_avg)) as avg_salary
from dbt_dev_gold.fct_jobs_daily
where is_active
group by 1 order by 1;
```
Line chart over time. **Cần ≥ 2 snapshot_dates** (chạy daily trong 1 tuần để thấy trend).

## Insights hiện tại (49 jobs)

- **SQL** xuất hiện trong 22% jobs — top skill DE/AI tại VN
- **Python** + **Data Analysis** tied at 14%
- **Senior HCMC** median: 28M VND/month
- **Hanoi senior** cao hơn HCMC: 35M (sample nhỏ, đợi data nhiều hơn)
- **Bosch** đang hiring nhiều nhất (6 jobs DE/AI)
- **Data Warehouse** skill có avg salary cao nhất: 67.5M VND/month
- **Intern** roles có lương rõ ràng (avg 6.25M)

## Schema drift detector

`silver_job_detail.city_canonical` test: NULL khi `city_raw_en is not null` → fail nếu city mới chưa add vào `city_map.csv`. Tương tự cho `salary_currency` (currently accepts VND/USD/JPY).

## Re-run sau parse mới

```bash
cd dbt_transform && DBT_PROFILES_DIR=. dbt build
```

Silver views auto-refresh (đọc trực tiếp). Gold tables rebuild. `fct_jobs_daily` chỉ append snapshot mới (không có today).

## Wire vào Prefect (optional)

Để dbt chạy tự động sau loader, add task vào `orchestration/flows/vnw_pipeline.py`:

```python
@task(name="dbt_build", retries=1)
def dbt_build():
    import subprocess
    r = subprocess.run(
        ["dbt", "build", "--project-dir", "/app/dbt_transform"],
        env={**os.environ, "DBT_PROFILES_DIR": "/app/dbt_transform"},
        capture_output=True, text=True,
    )
    if r.returncode != 0: raise RuntimeError(r.stderr)
```

Caveat: cần add `dbt-postgres==1.8.0` vào `orchestration/worker-requirements.txt` rồi rebuild image. Phase này tui giữ dbt build manual để worker image vẫn nhẹ.

## Phase tiếp

- **Skill canonical** (Python = python = py3) — seed `skill_canonical.csv`
- **Live FX rate** — Python task ghi vào `raw.fx_rates`
- **Snapshot history (SCD2)** — track salary/views thay đổi
- **Feature Store (Feast)** — gold tables là source
- **Cross-source dedup** — khi add TopCV/Glints
