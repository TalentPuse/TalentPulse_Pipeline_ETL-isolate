{{ config(materialized='view') }}

-- Silver: company dimension. One row per company_id with
-- aggregated info from their active postings.

with companies as (
    select
        company_id,
        company_name,
        company_logo_url,
        company_color,
        company_size_id,
        company_size_label,
        company_size_bucket,
        salary_vnd_monthly_avg,
        num_of_views,
        num_of_applications,
        city_canonical,
        region,
        parsed_at
    from {{ ref('silver_job_detail') }}
    where company_id is not null
),

ranked as (
    select
        *,
        row_number() over (partition by company_id order by parsed_at desc) as rn
    from companies
),

latest as (
    select * from ranked where rn = 1
),

aggregated as (
    select
        company_id,
        count(*)                                              as n_active_jobs,
        round(avg(salary_vnd_monthly_avg))                    as avg_salary_vnd,
        round(avg(num_of_views))                              as avg_views,
        round(avg(num_of_applications))                       as avg_apps
    from companies
    group by 1
)

select
    l.company_id,
    l.company_name,
    l.company_logo_url,
    l.company_color,
    l.company_size_id,
    l.company_size_label,
    l.company_size_bucket,
    l.city_canonical                                          as primary_city,
    l.region,
    a.n_active_jobs,
    a.avg_salary_vnd,
    a.avg_views,
    a.avg_apps,
    l.parsed_at                                               as last_seen_at
from latest l
join aggregated a using (company_id)
