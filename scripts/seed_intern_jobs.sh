#!/usr/bin/env bash
# seed_intern_jobs.sh — Insert mock intern/fresher jobs into dbt_dev_gold.fct_jobs_daily
# Usage: ./scripts/seed_intern_jobs.sh [count]
#   count: number of intern jobs to insert (default: 10)

set -euo pipefail

CONTAINER="${POSTGRES_CONTAINER:-talentpulse-postgres}"
DB_USER="${DB_USER:-admin}"
DB_NAME="${DB_NAME:-warehouse}"
SCHEMA="dbt_dev_gold"
TABLE="fct_jobs_daily"
COUNT="${1:-10}"

echo "==> Seeding $COUNT mock intern jobs into $SCHEMA.$TABLE ..."

docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" <<SQL
WITH mock_data AS (
    SELECT
        (ARRAY['vietnamworks','itviec'])[floor(random()*2+1)::int] AS src,
        'mock-intern-' || gs                                       AS jid,
        (ARRAY[
            'Thực tập sinh AI/ML Engineer',
            'Intern Backend Developer (Python)',
            'Thực tập sinh Data Engineer',
            'Software Engineering Intern',
            'Intern Frontend Developer (React)',
            'Thực tập sinh DevOps',
            'Data Analyst Intern',
            'Mobile Developer Intern (Flutter)',
            'Thực tập sinh QA/QC',
            'Cloud Engineering Intern',
            'Intern System Engineer',
            'Thực tập sinh PHP Developer',
            'Java Developer Intern',
            'Intern Blockchain Developer',
            'Thực tập sinh Fullstack Developer'
        ])[floor(random()*15+1)::int]                                AS title,
        (ARRAY[
            'AI Engineer','Backend Developer','Data Engineer',
            'Software Engineer','Frontend Developer','DevOps Engineer',
            'Data Analyst','Mobile Developer','QA Engineer','Cloud Engineer'
        ])[floor(random()*10+1)::int]                                AS cat,
        (ARRAY[
            'FPT Software','VNG Corporation','Tiki','Shopee','Grab',
            'Bosch','Samsung R&D','KMS Technology','NashTech','Sotatek',
            'Toss Lab','GMO-Z.com','Rakuten','NTT Data','Fujitsu'
        ])[floor(random()*15+1)::int]                                AS company,
        (ARRAY['1-50','51-200','201-500','501-1000'])[floor(random()*4+1)::int] AS size,
        3000000 + floor(random()*5000000)::numeric                   AS sal_min,
        6000000 + floor(random()*7000000)::numeric                   AS sal_max,
        4000000 + floor(random()*6000000)::numeric                   AS sal_avg,
        (ARRAY['Intern/Student','Fresher/Entry level'])[floor(random()*2+1)::int] AS lvl,
        (ARRAY['Ho Chi Minh City','Ha Noi','Da Nang'])[floor(random()*3+1)::int] AS city,
        (ARRAY['Southern','Northern','Central'])[floor(random()*3+1)::int]        AS region,
        (ARRAY['Bachelor','Student','Dont_require'])[floor(random()*3+1)::int]    AS degree,
        floor(random()*200)::int                                     AS views,
        floor(random()*30)::int                                      AS apps
    FROM generate_series(1, $COUNT) AS gs
)
INSERT INTO $SCHEMA.$TABLE (
    source, source_job_id, snapshot_date,
    title, job_category, company_id, company_name, company_size_bucket,
    salary_vnd_monthly_min, salary_vnd_monthly_max, salary_vnd_monthly_avg,
    job_level, city_canonical, region, degree_label,
    is_active, is_expired,
    num_of_views, num_of_applications,
    posted_at, expired_at, parsed_at
)
SELECT
    src, jid, CURRENT_DATE,
    title, cat, NULL, company, size,
    sal_min, sal_max, sal_avg,
    lvl, city, region, degree,
    true, false,
    views, apps,
    NOW() - (floor(random()*14)::int || ' days')::interval,
    NULL,
    NOW()
FROM mock_data
WHERE NOT EXISTS (
    SELECT 1 FROM $SCHEMA.$TABLE t WHERE t.source_job_id = mock_data.jid
);
SQL

echo ""
echo "==> Done. Intern job count by level:"
docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c \
    "SELECT job_level, COUNT(*) FROM $SCHEMA.$TABLE WHERE source_job_id LIKE 'mock-intern-%' GROUP BY job_level ORDER BY job_level;"

echo ""
echo "==> Sample rows:"
docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c \
    "SELECT source, source_job_id, title, company_name, job_level, city_canonical,
            round(salary_vnd_monthly_avg / 1000000.0, 1) as salary_m
     FROM $SCHEMA.$TABLE
     WHERE source_job_id LIKE 'mock-intern-%'
     ORDER BY posted_at DESC LIMIT 5;"
