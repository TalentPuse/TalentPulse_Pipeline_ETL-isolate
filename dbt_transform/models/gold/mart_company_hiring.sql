{{ config(materialized='table') }}

-- Gold: company hiring activity. Uses silver_job_detail instead of
-- silver_company_dim so we can compute salary/view means directly here.

select
    company_id,
    max(company_name)                                     as company_name,
    max(company_size_bucket)                              as company_size,
    max(company_size_label)                               as company_size_label,
    mode() within group (order by city_canonical)         as primary_city,
    mode() within group (order by region)                 as primary_region,
    count(*)                                              as n_jobs,
    round(avg(num_of_views))                              as avg_views,
    round(avg(num_of_applications))                       as avg_apps,
    round(avg(salary_vnd_monthly_avg))                    as avg_salary_vnd,
    round(min(salary_vnd_monthly_min))                    as min_salary_vnd,
    round(max(salary_vnd_monthly_max))                    as max_salary_vnd,
    max(parsed_at)                                        as last_seen_at,
    current_date                                          as snapshot_date
from {{ ref('silver_job_detail') }}
where company_id is not null
group by company_id
order by n_jobs desc, avg_views desc nulls last
