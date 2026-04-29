{{ config(
    materialized='incremental',
    unique_key=['source', 'source_job_id', 'snapshot_date']
) }}

-- Gold: one row per (job × snapshot_date). Incremental: each dbt run adds today's
-- snapshot if not already there. Build trend analytics on top (views growth,
-- salary drift, active/expired lifecycle).

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

{% if is_incremental() %}
where current_date not in (
    select distinct snapshot_date from {{ this }}
)
{% endif %}
