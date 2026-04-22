# Data Lineage

## Triết lý

> Lineage = "data này đến từ đâu, đi đâu, và ai phụ thuộc vào nó?"

3 câu hỏi lineage trả lời:
1. **Debug**: column trong `gold.mart_skill_demand` sai → trace ngược về source nào?
2. **Impact**: đổi schema `raw.job_detail` → model nào vỡ?
3. **Trust**: `feature.job_features.has_python` = tính từ đâu? ref_resolver.py parse hay đâu?

---

## Stack hiện tại

TalentPulse dùng **dbt native lineage** (column + model-level). Không cần DataHub/OpenMetadata cho 1 source.

### Xem lineage graph

```bash
cd dbt_transform
DBT_PROFILES_DIR=. dbt docs generate   # build manifest.json + catalog.json
DBT_PROFILES_DIR=. dbt docs serve      # UI at http://localhost:8080
```

Mở trình duyệt → tab "Lineage" hoặc nhấn vào bất kỳ model nào → "View Lineage Graph" → đồ thị DAG interactive.

### DAG TalentPulse hiện tại

```
raw.job_detail (source)
      │
      ▼
bronze.stg_job_detail
      │
      ▼
silver_job_detail ────┬──► silver_skill_long
      │               │         │
      │               │         ▼
      │               │    gold.mart_skill_demand
      │               │
      ├──► silver_company_dim
      │         │
      │         ▼
      ├──► gold.fct_jobs_daily
      ├──► gold.mart_company_hiring
      ├──► gold.mart_salary_by_level
      └──► feature.job_features ◄─── ML team
```

Mỗi arrow = `ref()` trong Jinja SQL → dbt auto-detect.

---

## Cái gì lineage dbt capture

| Capture | dbt native | Cần tool khác |
|---|---|---|
| Source → model | ✅ qua `source()` | — |
| Model → model | ✅ qua `ref()` | — |
| Column-level lineage | ✅ từ dbt 1.6+ | — |
| Tests linked to models | ✅ | — |
| External sources (MinIO → raw) | ❌ | DataHub / Airflow XCom |
| Row-level (which MinIO file → which row) | ❌ | `raw.crawl_log.minio_key` (DIY) |
| Cross-pipeline (Metabase → dbt) | ❌ | DataHub |

---

## Row-level lineage (pipeline outside dbt)

TalentPulse pipeline còn 1 phần upstream dbt không thấy:

```
VNW API → MinIO listings/          (crawler)
           │
           ▼
       MinIO details/*.html.gz     (detail crawler)
           │
           ▼
       MinIO parsed/*.json         (parser)
           │
           ▼
    raw.job_detail                 (loader)
```

Captured bằng **`raw.crawl_log`** table:
```sql
select
    source_job_id,
    minio_key_html,
    minio_key_parsed,
    http_status, crawled_at, parsed_at
from raw.crawl_log
where source_job_id = '2033447';
```

Kết hợp với dbt lineage → full E2E trace:
- MinIO file `parsed/details/vietnamworks/2033447.json`
- → `raw.job_detail`
- → `stg_job_detail` → `silver_job_detail`
- → `feature.job_features.has_python`

---

## Impact analysis

**Use case**: cô muốn đổi `salary_currency` → rename hay drop. Cần biết gì vỡ.

```bash
dbt ls --select +raw.job_detail.salary_currency  # all downstream
```

Trả về: `stg_job_detail`, `silver_job_detail`, `fct_jobs_daily`, `mart_salary_by_level`, `job_features`, etc. → quyết định safe migration.

**Ngược lại** (ai phụ thuộc model này):
```bash
dbt ls --select +silver_job_detail+   # upstream + downstream
```

---

## Tools phase sau (khi scale)

| Khi nào | Tool | Gì extra |
|---|---|---|
| Multi-source (TopCV, Glints) | **DataHub** | Cross-source lineage + glossary |
| Metabase → dbt lineage | **OpenLineage + Marquez** | Query-level lineage |
| Production SLA tracking | **Monte Carlo / Acceldata** | Data observability + freshness SLA |

Hiện tại dbt native đủ cho 49 jobs + 1 source.

---

## Commit strategy

- `target/manifest.json` + `target/catalog.json` = **auto-generated**, đã gitignore
- Mỗi dev tự chạy `dbt docs generate` khi cần xem lineage
- Alternative: CI build lineage → push lên S3/Pages → team xem không cần setup local

Không commit `target/` vì (a) nặng, (b) regen mỗi lần model đổi.
