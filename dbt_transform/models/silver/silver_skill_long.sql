{{ config(materialized='view') }}

-- Silver: unpack skills JSONB into one row per (job, skill).
-- Keeps the upstream VNW skill_id; canonical name mapping deferred to next phase.

select
    s.source,
    s.source_job_id,
    s.company_id,
    s.company_name,
    s.city_canonical,
    s.region,
    s.salary_vnd_monthly_avg,
    s.job_level,
    (skill->>'id')::bigint                       as skill_id,
    lower(trim(skill->>'name'))                  as skill_name_norm,
    skill->>'name'                               as skill_name_raw,
    coalesce((skill->>'weight')::int, 0)         as skill_weight,
    s.parsed_at
from {{ ref('silver_job_detail') }} s,
     jsonb_array_elements(s.skills) as skill
where jsonb_typeof(s.skills) = 'array'
  and (skill->>'name') is not null
  and trim(skill->>'name') != ''
