# Feature Store

## Why not Feast (yet)

Feast = production-grade ML feature store với:
- Offline store (training)
- Online store (serving <100ms)
- Point-in-time correctness (no leakage)
- Feature discovery + versioning

**Hiện tại**:
- 49 entities (jobs)
- Không có online serving (ML team train offline)
- 1 data freshness cadence (daily)

→ Feast sẽ **over-engineer**. Dùng **pragmatic path**: `feature.job_features` table build bằng dbt.

**Trigger upgrade sang Feast**:
- `> 10k` entities hoặc
- Cần sub-100ms lookup (realtime job recommender)
- Cần time-travel (reproducibility cho experiment)

---

## Current: `feature.job_features` (dbt table)

Location: `dbt_dev_feature.job_features`. Refreshed bằng `dbt build --select features+`.

### Schema

| Column | Type | Purpose |
|---|---|---|
| **source_job_id** | text | Entity key (1 row/job) |
| **snapshot_date** | date | Point-in-time key |
| salary_vnd_monthly_avg | numeric | **Target** cho salary model |
| has_salary_visible | bool | Filter/mask feature |
| has_python / has_sql / has_aws_cloud / has_ml_ai / has_bigdata / has_bi_tool | bool × 6 | Skill flags từ regex trên `silver_skill_long` |
| n_skills | int | Skill count |
| is_senior | bool | job_level has "senior/manager/lead/chief" |
| is_remote | bool | employment_type or title regex |
| job_level | text | Categorical (raw) |
| city_canonical | text | HCMC / Hanoi / ... |
| region | text | North / South / Central |
| degree_label | text | Bachelor / Master / ... |
| company_size_bucket | text | Micro / Small / Medium / Large / Enterprise |
| company_n_active_jobs | int | Signal for big hirers |
| num_of_views | int | Engagement |
| num_of_applications | int | Engagement |
| days_since_posted | int | Temporal (recency) |
| parsed_at | timestamptz | Metadata |

### dbt tests (9)
- `source_job_id` not_null + unique (entity invariant)
- `snapshot_date` not_null
- `n_skills` ∈ [0, 50]
- `salary_vnd_monthly_avg` ∈ [1M, 5B] when not null
- `is_senior, is_remote, has_python, has_sql` not_null

---

## How to consume

### Python (offline training)

```python
import pandas as pd
import psycopg2

conn = psycopg2.connect(
    host="localhost", port=5432, dbname="warehouse",
    user="admin", password="password"
)
df = pd.read_sql(
    "SELECT * FROM dbt_dev_feature.job_features",
    conn
)

# Separate features / target
target = 'salary_vnd_monthly_avg'
meta_cols = ['source_job_id', 'snapshot_date', 'parsed_at']

train = df[df[target].notna()]  # only jobs with visible salary
X = train.drop(columns=meta_cols + [target])
y = train[target]

# Encode categoricals
from sklearn.preprocessing import OneHotEncoder
cats = ['job_level', 'city_canonical', 'region', 'degree_label', 'company_size_bucket']
X_encoded = pd.get_dummies(X, columns=cats, drop_first=True)

# Train model
from sklearn.ensemble import GradientBoostingRegressor
model = GradientBoostingRegressor().fit(X_encoded, y)
```

### SQL (ad-hoc exploration)

```sql
-- Most in-demand skills right now
select skill_flag, count(*) filter (where flag) as n_jobs
from (
  select unnest(array['has_python','has_sql','has_ml_ai','has_bigdata']) as skill_flag,
         unnest(array[has_python,has_sql,has_ml_ai,has_bigdata]) as flag
  from dbt_dev_feature.job_features
) x group by 1 order by 2 desc;

-- Salary disparity: senior vs non-senior
select is_senior, count(*), avg(salary_vnd_monthly_avg)::bigint as avg_salary
from dbt_dev_feature.job_features
where salary_vnd_monthly_avg is not null
group by 1;
```

### dbt downstream (if ML predicts → dbt model)

```sql
-- dbt_transform/models/features/predicted_salary.sql
{{ config(materialized='table') }}
select source_job_id,
       salary_vnd_monthly_avg       as actual_salary,
       model_salary_prediction(/* features */) as predicted_salary,
       abs(salary_vnd_monthly_avg - model_salary_prediction(...)) as error
from {{ ref('job_features') }}
where salary_vnd_monthly_avg is not null
```

---

## Feature catalog (current)

```
Feature                      | Type   | Source table          | Derivation
-----------------------------|--------|-----------------------|-------------------------------
salary_vnd_monthly_avg       | num    | silver_job_detail     | salary × FX × period_multiplier
has_python / has_sql / ...   | bool   | silver_skill_long     | bool_or(skill_name regex)
n_skills                     | int    | silver_skill_long     | count(*) group by job
is_senior                    | bool   | silver_job_detail     | regex on job_level
is_remote                    | bool   | silver_job_detail     | regex on title/employment_type
company_n_active_jobs        | int    | silver_company_dim    | left join
days_since_posted            | int    | silver_job_detail     | current_date - posted_at
```

---

## Numbers hiện tại

```
n_jobs           : 49
with_salary      : 9 (18%)
has_python       : 7
has_sql          : 11
has_ml_ai        : 16
has_bigdata      : ?
is_senior        : 44 (90% — VNW khuynh hướng senior!)
is_remote        : 1 (hạn chế — chưa crawl job remote đúng cách)
avg_skills       : 5.0
avg_salary_vnd   : 28.75M/month
```

Insight: **90% jobs là senior** → model salary training bias về senior nếu không reweight.

---

## Migration path to Feast

Khi hit trigger (>10k entities hoặc online serving):

### Setup (~1 day, 200 LOC)

```
feature_repo/
├── feature_store.yaml
├── entities.py                  # Entity(name="job", ...)
├── data_sources.py              # PostgresSource(table="feature.job_features")
├── feature_views.py             # FeatureView definitions
└── feature_services.py          # Bundle of FVs per use case
```

`feature_store.yaml`:
```yaml
project: talentpulse
provider: local
registry: registry.db
online_store:
  type: redis
  connection_string: "redis:6379"
offline_store:
  type: postgres
  host: postgres
  database: warehouse
entity_key_serialization_version: 2
```

`entities.py`:
```python
from feast import Entity
job = Entity(name="job", join_keys=["source_job_id"])
```

`feature_views.py`:
```python
from datetime import timedelta
from feast import FeatureView, Field, PostgresSource
from feast.types import Bool, Int64, Float64, String

source = PostgresSource(
    name="job_features_source",
    query="SELECT * FROM dbt_dev_feature.job_features",
    timestamp_field="parsed_at",
)

job_core_v1 = FeatureView(
    name="job_core",
    entities=[job],
    ttl=timedelta(days=7),
    source=source,
    schema=[
        Field(name="has_python", dtype=Bool),
        Field(name="has_sql", dtype=Bool),
        Field(name="n_skills", dtype=Int64),
        Field(name="is_senior", dtype=Bool),
        Field(name="city_canonical", dtype=String),
        Field(name="salary_vnd_monthly_avg", dtype=Float64),
    ],
)
```

Then:
```bash
feast apply                                        # register
feast materialize-incremental $(date -I)           # push to Redis
```

### Add to docker-compose
```yaml
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
  feast:
    build: ./feature_repo
    depends_on: [postgres, redis]
    environment:
      FEAST_USAGE: "False"
```

---

## Anti-patterns tránh

- ❌ **Put features in `raw.*`**: raw = append-only, features = point-in-time override daily
- ❌ **1 feature = 1 column**: tuyệt đối không. 1 FV = ngữ nghĩa nhóm features related.
- ❌ **Train/serve skew**: SQL compute offline, Python compute online → bias. Feast giải bằng `get_online_features` dùng cùng registry.
- ❌ **No feature versioning**: `job_core_v1` → `v2` thay vì edit in-place → reproducibility ML experiment.

---

## Phase tiếp (khi scale)

- Cross-source features (mean salary across VNW + TopCV + Glints)
- Embeddings (description text → sentence-transformers vector)
- Temporal aggregates (7d/30d/90d job count per company → dùng SCD2 fct_jobs_daily)
- Feature monitoring (drift detection: distribution drift alert)
