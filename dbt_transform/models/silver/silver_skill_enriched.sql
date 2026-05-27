{{ config(materialized='view') }}

-- Silver: LLM-extracted skills from job descriptions (long format).
-- Source: raw.skill_extraction_log, unpacked into one row per (job, skill).

select
    e.source,
    e.source_job_id,
    parsed.skill_name,
    parsed.skill_name_raw,
    parsed.skill_category,
    parsed.importance,
    parsed.confidence,
    e.model_used,
    e.extracted_at
from {{ source('raw', 'skill_extraction_log') }} e,
     jsonb_array_elements(e.skills_json) as elem(skill_data)
cross join lateral (
    select
        lower(trim(elem.skill_data->>'name'))           as skill_name,
        elem.skill_data->>'name_raw'                     as skill_name_raw,
        coalesce(elem.skill_data->>'category', 'other')  as skill_category,
        coalesce(elem.skill_data->>'importance', 'mentioned') as importance,
        coalesce(elem.skill_data->>'confidence', 'medium') as confidence
) parsed
where jsonb_typeof(e.skills_json) = 'array'
  and elem.skill_data->>'name' is not null
  and trim(elem.skill_data->>'name') != ''
