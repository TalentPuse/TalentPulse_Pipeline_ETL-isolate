{{ config(materialized='table') }}

-- Gold: salary distribution (percentile) by (job_level × city_canonical).
-- Only uses jobs with visible salary already normalized to VND monthly.

select
    job_level,
    coalesce(city_canonical, 'Unknown')                   as city_canonical,
    coalesce(region, 'Unknown')                           as region,
    count(*)                                              as n_visible_jobs,
    round(percentile_cont(0.25) within group (order by salary_vnd_monthly_avg)) as p25_vnd,
    round(percentile_cont(0.50) within group (order by salary_vnd_monthly_avg)) as p50_vnd,
    round(percentile_cont(0.75) within group (order by salary_vnd_monthly_avg)) as p75_vnd,
    round(avg(salary_vnd_monthly_avg))                    as avg_salary_vnd,
    round(min(salary_vnd_monthly_min))                    as min_salary_vnd,
    round(max(salary_vnd_monthly_max))                    as max_salary_vnd,
    current_date                                          as snapshot_date
from {{ ref('silver_job_detail') }}
where job_level is not null
  and salary_vnd_monthly_avg is not null
group by 1, 2, 3
having count(*) >= 1
order by job_level, city_canonical
