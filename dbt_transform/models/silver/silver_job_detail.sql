{{ config(materialized='view') }}

-- Silver: normalize salary to VND-monthly + join lookup tables.
-- Salary visible jobs only get a non-null salary_vnd_monthly_*; rest are NULL.

with raw as (
    select * from {{ ref('stg_job_detail') }}
),

-- Normalization engine results (coalesced with legacy logic below)
norm as (
    select distinct on (source, source_job_id)
        source as norm_source,
        source_job_id as norm_source_job_id,
        job_category as norm_job_category,
        category_method,
        category_confidence
    from normalization.job_normalization
    order by source, source_job_id, run_at desc
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

-- City resolution: exact alias first, then substring.
--
-- The exact join alone left 688 rows (24.7% of LinkedIn) with a NULL city.
-- LinkedIn does not send a city, it sends a whole place string —
-- "Củ Chi, Ho Chi Minh City, Vietnam", "Trần Văn Thời, Ca Mau, Vietnam" — so an
-- equality join can never match it, no matter how many aliases the seed lists.
-- Matching on containment instead resolves those, with the LONGEST alias winning
-- so that a more specific entry is never shadowed by a shorter one.
--
-- Rows that stay NULL after this are genuinely unresolvable: 217 of them have
-- city_raw_en = 'Vietnam', which names no city at all. That is why city_canonical
-- carries no not_null test — NULL here is information, not a defect.
city_resolved as (
    select
        ce.source,
        ce.source_job_id,
        coalesce(exact_m.city_canonical, fuzzy.city_canonical) as city_canonical,
        coalesce(exact_m.region, fuzzy.region)                 as region
    from city_extract ce
    left join {{ ref('city_map') }} exact_m
        on exact_m.city_raw_en = ce.city_raw_en
    left join lateral (
        select m.city_canonical, m.region
        from {{ ref('city_map') }} m
        where ce.city_raw_en is not null
          and lower(ce.city_raw_en) like '%' || lower(m.city_raw_en) || '%'
        order by length(m.city_raw_en) desc
        limit 1
    ) fuzzy on true
),

-- VietnamWorks ships its own job taxonomy in job_function, and every VNW row has
-- one. This used to be a hardcoded CASE over exactly THREE of ~70 function names,
-- so the other ~67 were thrown away and those jobs fell through to 'Other'.
-- The mapping now lives in a seed. `beats_title` marks the original three, which
-- stay AHEAD of the title keyword lookup (they were chosen because title matching
-- is unreliable for sales roles); everything else is consulted only after title
-- matching has failed, since a title is more specific than a function bucket.
vnw_category as (
    select
        r.source,
        r.source_job_id,
        m.job_category as source_category,
        m.beats_title
    from raw r
    join {{ ref('vnw_function_category_map') }} m
      on m.vnw_function = r.job_function::jsonb->'children'->0->>'name'
    where r.source = 'vietnamworks'
      and r.job_function is not null
),

category_resolved as (
    select
        r.source,
        r.source_job_id,
        -- Deliberately NO 'Other' fallback here. It used to be the last argument
        -- of this coalesce, which made the column non-nullable — and that in turn
        -- made `coalesce(cr.job_category, n.norm_job_category)` below dead code:
        -- the normalization engine's answer could never be reached, so all 159
        -- rules in normalization.category_rule had no effect on the warehouse.
        -- 'Other' now lives at the end of that chain instead, where it belongs.
        coalesce(
            case when vc.beats_title then vc.source_category end,
            (
                select tcm.job_category
                from {{ ref('job_title_category_map') }} tcm
                where lower(r.title) like '%' || tcm.keyword || '%'
                order by tcm.priority asc
                limit 1
            ),
            vc.source_category
        ) as job_category
    from raw r
    left join vnw_category vc
        on vc.source = r.source and vc.source_job_id = r.source_job_id
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
        -- Hourly-but-actually-monthly sanity check, compared in VND.
        --
        -- This used to test `r.salary_min > 10000000` against the RAW figure,
        -- which silently assumed the currency was VND. A VietnamWorks posting
        -- tagged Hourly with salary_min = 2000 USD slipped straight past it (2000
        -- is not > 10M) and got multiplied by 172, landing in the warehouse at
        -- 15,050,000,000 VND/month. Converting first makes the test currency-blind:
        -- 2000 USD/h is ~50M VND/h, obviously a monthly figure mislabelled.
        case when r.salary_period_id = 2
                  and r.salary_min * coalesce(fx.vnd_rate, 1) > 10000000
            then 1 else r.salary_period_id
        end as salary_period_id,
        spm.period_label                      as salary_period_label,
        -- Normalize the salary band to VND-monthly. NULL when the salary is not
        -- visible, the currency has no FX rate, or the band is missing.
        -- multiplier: Monthly=1, Hourly=172, Yearly=1/12 (from salary_period_map).
        --
        -- Values outside 1M..5B VND/month are dropped to NULL rather than
        -- published. Sources do emit nonsense — six VietnamWorks rows carried
        -- 9, 650, 1000 and 10000 VND/month — and a bogus number is worse than a
        -- missing one here, because these columns feed the salary marts where a
        -- 12 VND row drags the percentiles down for everybody.
        {%- set band = "between 1000000 and 5000000000" %}
        case
            when r.is_salary_visible
             and r.salary_min is not null
             and fx.vnd_rate is not null
             and round(r.salary_min * fx.vnd_rate * {{ salary_multiplier() }}) {{ band }}
            then round(r.salary_min * fx.vnd_rate * {{ salary_multiplier() }})
        end                                    as salary_vnd_monthly_min,
        case
            when r.is_salary_visible
             and r.salary_max is not null
             and fx.vnd_rate is not null
             and round(r.salary_max * fx.vnd_rate * {{ salary_multiplier() }}) {{ band }}
            then round(r.salary_max * fx.vnd_rate * {{ salary_multiplier() }})
        end                                    as salary_vnd_monthly_max,
        case
            when r.is_salary_visible
             and r.salary_min is not null
             and r.salary_max is not null
             and fx.vnd_rate is not null
             and round(((r.salary_min + r.salary_max) / 2.0) * fx.vnd_rate * {{ salary_multiplier() }}) {{ band }}
            then round(((r.salary_min + r.salary_max) / 2.0) * fx.vnd_rate * {{ salary_multiplier() }})
        end                                    as salary_vnd_monthly_avg,
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
            'Mid-level'
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
        coalesce(cr.job_category, n.norm_job_category, 'Other') as job_category,
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
        cr_city.city_canonical,
        cr_city.region,
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
    left join city_resolved cr_city
        on cr_city.source = r.source and cr_city.source_job_id = r.source_job_id
    left join {{ ref('degree_map') }} dm
        on dm.highest_degree_id = r.highest_degree_id
    left join {{ ref('company_size_map') }} csm
        on csm.company_size_id = r.company_size_id
    left join category_resolved cr
        on cr.source = r.source and cr.source_job_id = r.source_job_id
    left join level_resolved lr
        on lr.source = r.source and lr.source_job_id = r.source_job_id
    left join norm n
        on n.norm_source = r.source and n.norm_source_job_id = r.source_job_id
)

select * from joined
