# Data Audit & Quality

Tài liệu mô tả triết lý audit, các rules đang chạy, và cách điều tra reject.

## Triết lý

> **"Trust nothing. Quarantine everything suspect. Replay anytime."**

Pipeline TalentPulse focus DE/AI. Tất cả data đi qua 3 lớp guard:

```
crawler ──── parser ────[validators]──── loader ──── raw.job_detail (clean)
                              │
                              └─── reject ──── raw.job_detail_rejects
                                              (audit, replay)
```

- **Source** (listing API): server-side filter `jobFunctionsV3.jobFunctionV3Id=27`
  → giảm noise trước khi crawl
- **Loader** (`src/loaders/validators.py`): kiểm 2 lớp
  - **Hard rules** (BAD_SALARY_RANGE, BAD_DATE_RANGE, MISSING_TITLE, MISSING_COMPANY)
  - **Focus filter** (OUT_OF_FOCUS) — extra safety net
- **dbt tests** (`dbt_transform/`): chạy sau load để verify invariants
  (not_null, unique, accepted_values)

---

## Validation rules hiện tại

| Code | When | Severity |
|---|---|---|
| `MISSING_TITLE` | `payload.title` rỗng/null | Hard reject |
| `MISSING_COMPANY` | `payload.company_name` rỗng/null | Hard reject |
| `BAD_SALARY_RANGE` | `is_salary_visible AND salary_min > salary_max` | Hard reject |
| `BAD_DATE_RANGE` | `expired_at < posted_at` | Hard reject |
| `OUT_OF_FOCUS` | `job_function.children[*].id ∉ {27}` | Focus reject |

Hard rules win over focus (xem `validate()` trong `validators.py`). Logic:
một row vừa miss title vừa off-topic → log `MISSING_TITLE` (bug nguồn quan
trọng hơn phân loại).

Allowed function IDs hiện tại: **27** (`Data Engineer/Data Analyst/AI`).
Sửa trong `src/loaders/validators.py::ALLOWED_FUNCTION_IDS` nếu mở rộng.

---

## Quarantine table: `raw.job_detail_rejects`

```sql
SELECT id, source, source_job_id, parser_version,
       reject_reason, reject_detail,
       payload->>'title' AS title,
       rejected_at
FROM raw.job_detail_rejects
ORDER BY rejected_at DESC;
```

Schema:
- `payload JSONB` — full parsed payload, cho phép replay sau khi sửa rule
- `minio_key` — link về parsed JSON gốc trong MinIO
- `reject_reason` (indexed) — code (OUT_OF_FOCUS, BAD_SALARY_RANGE, ...)
- `reject_detail` — human-readable string (vd `function='NGO/Non-Profit' parentId=25`)
- `rejected_at` (indexed) — khi reject

**Append-only**. Không xoá khi re-run. Cùng job có thể xuất hiện nhiều lần
nếu reject ở các parser_version khác nhau.

---

## Câu hỏi audit cốt lõi (SQL ready)

### 1. Today's reject summary
```sql
SELECT reject_reason, count(*) AS n
FROM raw.job_detail_rejects
WHERE rejected_at >= now() - interval '24 hours'
GROUP BY 1 ORDER BY 2 DESC;
```

### 2. Rejected-but-clean-history (a job that USED to be valid but suddenly rejects)
```sql
SELECT r.source_job_id, r.reject_reason, r.rejected_at,
       d.parsed_at AS last_clean_parse
FROM raw.job_detail_rejects r
LEFT JOIN raw.job_detail d USING (source, source_job_id)
WHERE d.source_job_id IS NOT NULL
ORDER BY r.rejected_at DESC;
```
→ Detector cho **upstream schema drift** (VNW thay đổi phân loại job_function).

### 3. Top reject reasons last 7 days
```sql
SELECT reject_reason, count(*),
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM raw.job_detail_rejects
WHERE rejected_at >= now() - interval '7 days'
GROUP BY 1 ORDER BY 2 DESC;
```

### 4. Functions hay bị OUT_OF_FOCUS (tune allow-list)
```sql
SELECT reject_detail, count(*)
FROM raw.job_detail_rejects
WHERE reject_reason = 'OUT_OF_FOCUS'
GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
```
→ Nếu thấy function ý nghĩa lặp đi lặp lại (vd "Database Administration"),
xem xét add vào `ALLOWED_FUNCTION_IDS`.

### 5. Coverage check vs upstream
```sql
SELECT
  (SELECT count(*) FROM raw.crawl_log WHERE status='success') AS crawled,
  (SELECT count(*) FROM raw.job_detail) AS loaded_clean,
  (SELECT count(*) FROM raw.job_detail_rejects) AS rejected,
  (SELECT count(*) FROM raw.crawl_log WHERE status='success') -
  (SELECT count(*) FROM raw.job_detail) -
  (SELECT count(*) FROM raw.job_detail_rejects) AS unaccounted;
```
→ `unaccounted` should be 0. >0 = parser failed, manual investigation.

---

## Replay flow

Sau khi tune rule (vd add function id 28 vào allow-list):

```bash
# 1. Update ALLOWED_FUNCTION_IDS in src/loaders/validators.py
# 2. Re-run loader — same MinIO data, fresh validation pass
PYTHONPATH=. .venv/Scripts/python.exe -m src.loaders.job_detail_loader

# 3. Cleanup historical rows now off-spec OR previously rejected but now valid
docker exec talentpulse-postgres psql -U admin -d warehouse -c "
delete from raw.job_detail d
where (d.source, d.source_job_id) in (
  select source, source_job_id from raw.job_detail_rejects
  where rejected_at > now() - interval '1 hour'  -- only fresh rejects
);
"

# 4. Re-run dbt tests
cd dbt_transform && DBT_PROFILES_DIR=. dbt build
```

Vì `payload JSONB` đầy đủ, có thể **back-fill chính** từ rejects table mà
không cần re-crawl HTML:

```sql
-- Promote a previously-rejected row back to job_detail (manual override)
INSERT INTO raw.job_detail (source, source_job_id, ...)
SELECT source, source_job_id, payload->>'title', ...
FROM raw.job_detail_rejects WHERE id = 42;
```

(Phase này chưa build helper — viết tay khi cần.)

---

## dbt tests (post-load invariants)

`dbt_transform/models/bronze/_stg_job_detail__tests.yml`:

| Test | Column | Expectation |
|---|---|---|
| not_null | source_job_id | Always present |
| unique | source_job_id | No dup |
| not_null | source, title, company_name, parsed_at | Loader's hard rules |
| accepted_values | parser_version | `v1` or `v2` (alert when bumping) |
| accepted_values | salary_currency | `VND` / `USD` / `JPY` (drift detector) |

Run:
```bash
cd dbt_transform && DBT_PROFILES_DIR=. dbt build
```

Nếu thêm currency mới (KRW, SGD) → `accepted_values` fail → add vào list.
Đây là **intentional friction** — bắt mình review.

---

## Anti-patterns đã tránh

- ❌ **Silent drop**: không bao giờ skip row mà không log/quarantine
- ❌ **Overwrite without trace**: rejects append-only, không UPSERT
- ❌ **Validation in DB triggers**: giữ logic ở Python, dễ test/version
- ❌ **One giant validator function**: tách `validate_focus` + `validate_business_rules` (pure, ≤80 LOC each)

---

## Numbers hiện tại (sau audit phase)

```
Source listing (VNW API w/ function filter):  ~50 jobs/page, all in-focus
Loaded clean (raw.job_detail):                49 (100% DE/AI)
Quarantined (raw.job_detail_rejects):         20 (all OUT_OF_FOCUS, historical)
dbt tests:                                    8/8 PASS
Pytest suite:                                 91/91 PASS
```

Noise giảm từ **29% → 0%** trên data mới.

---

## Phase tiếp (out of scope hiện tại)

- **Silver normalization**: USD→VND, monthly conversion, city canonical
- **SCD2 history**: track salary/views thay đổi theo ngày
- **Cross-source dedup**: khi add TopCV/Glints
- **Soft warnings**: rules cảnh báo (vd salary < 5M VND/tháng) chứ không reject
- **Reject auto-replay**: cron re-evaluate rejects khi rules thay đổi
