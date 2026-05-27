{{ config(materialized='table') }}

-- Gold: weekly skill demand trends.
-- Tracks how skill demand changes over time.
-- Useful for: "RAG demand grew 40% this month"

with daily_jobs as (
    select
        source,
        source_job_id,
        salary_vnd_monthly_avg,
        snapshot_date
    from {{ ref('fct_jobs_daily') }}
    where is_active
),

skills as (
    select source, source_job_id, skill_name
    from {{ ref('silver_skill_unified') }}
),

joined as (
    select
        s.skill_name,
        date_trunc('week', d.snapshot_date)::date as week,
        count(distinct d.source_job_id) as n_jobs,
        round(avg(d.salary_vnd_monthly_avg) / 1000000.0, 1) as avg_salary_m
    from skills s
    join daily_jobs d
        on d.source = s.source and d.source_job_id = s.source_job_id
    group by s.skill_name, date_trunc('week', d.snapshot_date)
)

select
    skill_name as skill,
    week,
    n_jobs,
    avg_salary_m
from joined
where n_jobs >= 2
order by skill_name, week
