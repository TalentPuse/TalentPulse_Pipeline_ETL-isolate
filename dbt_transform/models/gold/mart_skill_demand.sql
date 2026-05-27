{{ config(materialized='table') }}

-- Gold: demand signal per skill for IT/AI/Data jobs only.
-- n_jobs = number of IT/AI jobs mentioning the skill;
-- pct_of_jobs = share of total IT/AI jobs; avg_salary_vnd =
-- average monthly salary (VND) of jobs with the skill that have visible salary.

with jobs as (
    select * from {{ ref('silver_job_detail') }}
    where job_category in (
        'Data Engineer', 'Data Analyst', 'AI Engineer',
        'Data Scientist', 'Backend Developer', 'Other'
    )
),
skills_long as (
    select * from {{ ref('silver_skill_long') }}
),
total as (
    select count(*)::numeric as total_jobs from jobs
)

select
    s.skill_name_norm                                         as skill,
    count(distinct s.source_job_id)                           as n_jobs,
    round(100.0 * count(distinct s.source_job_id) / total.total_jobs, 1) as pct_of_jobs,
    round(avg(j.salary_vnd_monthly_avg))                      as avg_salary_vnd,
    round(avg(j.salary_vnd_monthly_avg)
          filter (where j.job_level in ('Manager', 'Director+')))  as avg_salary_manager_vnd,
    round(avg(j.salary_vnd_monthly_avg)
          filter (where j.job_level = 'Senior'))                   as avg_salary_senior_vnd,
    round(avg(s.skill_weight), 1)                             as avg_weight,
    current_date                                              as snapshot_date
from skills_long s
join jobs j on j.source_job_id = s.source_job_id
cross join total
group by s.skill_name_norm, total.total_jobs
having count(distinct s.source_job_id) >= 2
order by n_jobs desc
