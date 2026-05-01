{{ config(materialized='view') }}

-- Silver: normalize salary to VND-monthly + join lookup tables.
-- Salary visible jobs only get a non-null salary_vnd_monthly_*; rest are NULL.

with raw as (
    select * from {{ ref('stg_job_detail') }}
),

city_extract as (
    select
        source,
        source_job_id,
        nullif(locations->0->>'city', '')     as city_raw_en,
        nullif(locations->0->>'city_vi', '')  as city_raw_vi,
        nullif(locations->0->>'address', '')  as primary_address_extracted,
        (locations->0->>'city_id')::int       as city_id_raw
    from raw
),

category_resolved as (
    select
        r.source,
        r.source_job_id,
        (
            select tcm.job_category
            from {{ ref('job_title_category_map') }} tcm
            where lower(r.title) like '%' || tcm.keyword || '%'
            order by tcm.priority asc
            limit 1
        ) as job_category
    from raw r
),

level_resolved as (
    select
        r.source,
        r.source_job_id,
        (
            select jlm.job_level
            from {{ ref('job_level_map') }} jlm
            where lower(r.title) like '%' || jlm.keyword || '%'
            order by jlm.priority asc
            limit 1
        ) as title_job_level
    from raw r
),

joined as (
    select
        r.source,
        r.source_job_id,
        r.title,
        r.alias,
        r.company_id,
        r.company_name,
        r.company_logo_url,
        r.company_color,
        r.company_size_id,
        csm.size_label                        as company_size_label,
        csm.size_bucket                       as company_size_bucket,
        r.is_salary_visible,
        r.salary_currency,
        r.salary_period_id,
        spm.period_label                      as salary_period_label,
        r.salary_min                          as salary_min_raw,
        r.salary_max                          as salary_max_raw,
        r.pretty_salary,
        r.pretty_salary_vi,
        r.pretty_salary_en,
        -- Normalized to VND monthly. Multiply by FX rate then by months_multiplier.
        case when r.is_salary_visible and r.salary_min is not null then
            round((r.salary_min * coalesce(fx.vnd_rate, 1) * coalesce(spm.months_multiplier, 1))::numeric)
        end as salary_vnd_monthly_min,
        case when r.is_salary_visible and r.salary_max is not null then
            round((r.salary_max * coalesce(fx.vnd_rate, 1) * coalesce(spm.months_multiplier, 1))::numeric)
        end as salary_vnd_monthly_max,
        case when r.is_salary_visible and r.salary_min is not null and r.salary_max is not null then
            round((((r.salary_min + r.salary_max) / 2.0)
                * coalesce(fx.vnd_rate, 1) * coalesce(spm.months_multiplier, 1))::numeric)
        end as salary_vnd_monthly_avg,
        coalesce(
            case r.job_level
                when 'Intern/Student'       then 'Intern/Student'
                when 'Fresher/Entry level'  then 'Fresher/Entry level'
                when 'Mid-level'            then 'Mid-level'
                when 'Senior'               then 'Senior'
                when 'Manager'              then 'Manager'
                when 'Director+'            then 'Director+'
                when 'Internship'           then 'Intern/Student'
                when 'Entry level'          then 'Fresher/Entry level'
                when 'Associate'            then 'Fresher/Entry level'
                when 'Executive'            then 'Director+'
                when 'Director'             then 'Director+'
                when 'Director and above'   then 'Director+'
            end,
            lr.title_job_level,
            case when r.job_level = 'Experienced (non-manager)' then 'Mid-level' end
        ) as job_level,
        r.job_level                           as job_level_raw,
        r.years_of_experience,
        r.employment_type,
        r.job_function,
        r.locations,
        r.industries,
        r.skills,
        r.benefits,
        r.services,
        r.job_description_text,
        r.job_requirement_text,
        r.posted_at,
        r.expired_at,
        r.last_updated_at,
        r.is_expired_calc                     as is_expired,
        r.is_active_calc                      as is_active,
        r.online_on,
        r.num_of_views,
        r.num_of_applications,
        r.num_of_recruits,
        r.working_days,
        r.working_from_hour,
        r.working_to_hour,
        r.highest_degree_id,
        dm.degree_label,
        cr.job_category,
        r.language_selected,
        r.language_selected_vi,
        r.range_age,
        r.required_resume,
        r.required_cover_letter,
        r.primary_address,
        r.contact_name,
        r.contact_email,
        r.canonical_slug,
        r.source_url,
        ce.city_raw_en,
        ce.city_raw_vi,
        ce.primary_address_extracted,
        cm.city_canonical,
        cm.region,
        r.parser_version,
        r.parsed_at,
        r.loaded_at
    from raw r
    left join city_extract ce
        on ce.source = r.source and ce.source_job_id = r.source_job_id
    left join {{ ref('fx_rates') }} fx
        on fx.currency = r.salary_currency
    left join {{ ref('salary_period_map') }} spm
        on spm.salary_period_id = r.salary_period_id
    left join {{ ref('city_map') }} cm
        on cm.city_raw_en = ce.city_raw_en
    left join {{ ref('degree_map') }} dm
        on dm.highest_degree_id = r.highest_degree_id
    left join {{ ref('company_size_map') }} csm
        on csm.company_size_id = r.company_size_id
    left join category_resolved cr
        on cr.source = r.source and cr.source_job_id = r.source_job_id
    left join level_resolved lr
        on lr.source = r.source and lr.source_job_id = r.source_job_id
)

select * from joined
