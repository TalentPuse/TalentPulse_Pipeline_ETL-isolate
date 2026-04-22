# Metabase Dashboard Setup

Governance/BI dashboard cho TalentPulse. Đọc read-only từ Postgres `raw` schema.

## Start

```bash
docker compose up -d metabase
# Wait ~20-60s for boot
curl http://localhost:3000/api/health   # expect {"status":"ok"}
```

Open: **http://localhost:3000**

## Initial setup wizard (1 lần)

1. Create admin account (lưu password)
2. "Add your data" → **PostgreSQL**
   - Display name: `TalentPulse Warehouse`
   - Host: `postgres` (docker network DNS — KHÔNG phải localhost)
   - Port: `5432`
   - Database name: `warehouse`
   - Username: `metabase_ro`
   - Password: `metabase_ro_pw`
   - SSL: off
3. Skip usage stats, finish

Metabase sẽ auto-scan schema và liệt kê tables. Ready to query.

## Read-only user

Tạo 1 lần:
```bash
docker exec -i talentpulse-postgres psql -U admin -d warehouse < scripts/create_metabase_user.sql
```

User `metabase_ro`:
- SELECT trên toàn bộ `raw.*` (hiện tại + future tables)
- Không có DDL, không write
- Password mặc định `metabase_ro_pw` — **đổi trước khi prod**

## 5 Starter queries (paste vào Metabase → New → SQL Query)

### 1. Overview KPIs
```sql
select
  count(*)                                      as total_jobs,
  count(distinct company_id)                    as unique_companies,
  count(*) filter (where is_salary_visible)     as salary_visible,
  round(100.0 * count(*) filter (where is_salary_visible) / count(*), 1) as salary_visible_pct,
  count(*) filter (where is_expired)            as expired_jobs,
  max(parsed_at)                                as last_parse
from raw.job_detail;
```
→ Chart type: **Scalar** (1 big number) hoặc **Table**

### 2. Top skills in DE/AI (bar chart)
```sql
select skill->>'name' as skill_name, count(*) as jobs
from raw.job_detail, jsonb_array_elements(skills) skill
where job_function->'children'->0->>'name' ilike '%Data%'
   or job_function->'children'->0->>'name' ilike '%AI%'
group by 1
order by 2 desc
limit 15;
```
→ Chart type: **Bar**, X = `skill_name`, Y = `jobs`

### 3. Salary distribution (VND, monthly, visible jobs)
```sql
select
  case
    when salary_min < 10000000  then '< 10M'
    when salary_min < 20000000  then '10-20M'
    when salary_min < 30000000  then '20-30M'
    when salary_min < 50000000  then '30-50M'
    else '50M+'
  end as salary_bucket,
  count(*) as jobs
from raw.job_detail
where is_salary_visible
  and salary_currency = 'VND'
  and salary_period_id = 1   -- monthly
group by 1
order by min(salary_min);
```
→ Chart type: **Bar**

### 4. Top companies hiring DE/AI
```sql
select
  company_name,
  count(*)                    as jobs,
  max(company_size)           as size,
  sum(num_of_views)           as total_views,
  sum(num_of_applications)    as total_apps
from raw.job_detail
where job_function->'children'->0->>'name' ilike '%Data%'
   or job_function->'children'->0->>'name' ilike '%AI%'
group by 1
order by 2 desc
limit 10;
```
→ Chart type: **Table**

### 5. Crawl pipeline health (ops monitoring lite)
```sql
select
  status,
  count(*)                                     as jobs,
  count(*) filter (where http_status = 200)    as http_200,
  count(*) filter (where http_status >= 400)   as http_errors,
  max(crawled_at)                              as last_success,
  round(avg(retry_count)::numeric, 2)          as avg_retries
from raw.crawl_log
group by 1;
```
→ Chart type: **Table** + email alert nếu `http_errors > 5`

## Recommended dashboard layout

```
┌──────────────────────────────────────────────────────────────┐
│  DE/AI Market Overview                                       │
├──────────────────────────┬───────────────────────────────────┤
│  [1] Overview KPIs       │  [5] Crawl health                 │
│  (4 scalar numbers)      │  (table)                          │
├──────────────────────────┴───────────────────────────────────┤
│  [2] Top skills (bar, last 30d)                              │
├──────────────────────────────────────────────────────────────┤
│  [3] Salary dist           │  [4] Top companies              │
│  (bar)                     │  (table with drill-down)        │
└──────────────────────────────────────────────────────────────┘
```

## Troubleshooting

**Metabase can't connect to Postgres**
- Host = `postgres` (container name), KHÔNG phải `localhost` — vì metabase container dùng docker DNS
- Kiểm tra: `docker exec talentpulse-metabase ping -c 2 postgres`

**"password authentication failed"**
- Chưa chạy `create_metabase_user.sql`
- Hoặc password khác — check `scripts/create_metabase_user.sql`

**Saved questions trả 0 rows**
- Parser version v2 chưa load? Chạy lại `src.loaders.job_detail_loader`

## Next: lock down trước prod

- [ ] Đổi password `metabase_ro_pw` → secret qua env
- [ ] Tạo admin Metabase không dùng default email
- [ ] Backup Metabase app data (`metabase_app` DB) cùng Postgres backup
- [ ] HTTPS reverse proxy (Caddy/Traefik) nếu expose public
