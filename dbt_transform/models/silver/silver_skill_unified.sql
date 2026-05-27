{{ config(materialized='view') }}

-- Silver: unified skills from source tags + LLM extraction.
-- Deduplicates using skill_synonym normalization where available.
-- Priority: source_tag > llm extraction (kept via DISTINCT ON).

with source_tags as (
    select
        source,
        source_job_id,
        skill_name_norm as skill_name,
        skill_name_raw,
        'source_tag'::text as extraction_method,
        skill_weight,
        null::text as skill_category,
        null::text as importance,
        parsed_at as extracted_at
    from {{ ref('silver_skill_long') }}
),

llm_extracted as (
    select
        source,
        source_job_id,
        skill_name,
        skill_name_raw,
        'llm'::text as extraction_method,
        case importance
            when 'required' then 80
            when 'preferred' then 50
            else 20
        end as skill_weight,
        skill_category,
        importance,
        extracted_at
    from {{ ref('silver_skill_enriched') }}
),

combined as (
    select * from source_tags
    union all
    select * from llm_extracted
)

select distinct on (source, source_job_id, skill_name)
    source,
    source_job_id,
    skill_name,
    skill_name_raw,
    extraction_method,
    skill_weight,
    skill_category,
    importance,
    extracted_at
from combined
order by source, source_job_id, skill_name, skill_weight desc
