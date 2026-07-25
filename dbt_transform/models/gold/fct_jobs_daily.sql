{{ config(
    materialized='incremental',
    unique_key=['source', 'source_job_id', 'snapshot_date'],
    incremental_strategy='delete+insert'
) }}

-- Gold: one row per (job × snapshot_date). Incremental with delete+insert on the
-- unique key: every run rebuilds TODAY's snapshot from the full current silver
-- (delete today's rows for the incoming jobs, re-insert) while preserving past
-- days. Build trend analytics on top (views growth, salary drift, lifecycle).
--
-- NB: do NOT re-add a `where current_date not in (select snapshot_date ...)`
-- guard. It froze the gold layer at the size of the day's FIRST dbt run — every
-- later run in the same day inserted 0 rows, dropping jobs crawled afterwards.

with today_snapshot as (
    select
        source,
        source_job_id,
        current_date                         as snapshot_date,
        title,
        job_category,
        company_id,
        company_name,
        company_size_bucket,
        salary_vnd_monthly_min,
        salary_vnd_monthly_max,
        salary_vnd_monthly_avg,
        job_level,
        city_canonical,
        region,
        degree_label,
        is_active,
        is_expired,
        num_of_views,
        num_of_applications,
        posted_at,
        expired_at,
        parsed_at
    from {{ ref('silver_job_detail') }}
)

select * from today_snapshot
