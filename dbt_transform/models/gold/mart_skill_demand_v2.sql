{{ config(materialized='table') }}

-- Gold: enriched skill demand analytics.
-- Adds skill_category, importance breakdown, extraction method stats.

with jobs as (
    select * from {{ ref('silver_job_detail') }}
),
skills as (
    select * from {{ ref('silver_skill_unified') }}
),
total as (
    select count(*)::numeric as total_jobs from jobs
)

select
    s.skill_name                                            as skill,
    max(s.skill_category)                                    as skill_category,
    count(distinct s.source_job_id)                          as n_jobs,
    round(100.0 * count(distinct s.source_job_id)
          / total.total_jobs, 1)                             as pct_of_jobs,
    round(avg(j.salary_vnd_monthly_avg))                     as avg_salary_vnd,
    round(avg(j.salary_vnd_monthly_avg)
          filter (where j.job_level in ('Manager', 'Director+')))
                                                             as avg_salary_manager_vnd,
    round(avg(j.salary_vnd_monthly_avg)
          filter (where j.job_level = 'Senior'))             as avg_salary_senior_vnd,
    count(*) filter (where s.importance = 'required')        as n_required,
    count(*) filter (where s.importance = 'preferred')       as n_preferred,
    count(*) filter (where s.extraction_method = 'llm')      as n_llm_extracted,
    count(*) filter (where s.extraction_method = 'source_tag') as n_source_tagged,
    current_date                                             as snapshot_date
from skills s
join jobs j
    on j.source = s.source and j.source_job_id = s.source_job_id
cross join total
group by s.skill_name, total.total_jobs
having count(distinct s.source_job_id) >= 2
order by n_jobs desc
