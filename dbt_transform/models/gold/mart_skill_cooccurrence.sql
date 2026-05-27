{{ config(materialized='table') }}

-- Gold: skill co-occurrence matrix.
-- Shows which skills frequently appear together in the same job.
-- Useful for: "people who know Python also know X"

with job_skills as (
    select
        source,
        source_job_id,
        skill_name
    from {{ ref('silver_skill_unified') }}
),

pairs as (
    select
        a.skill_name as skill_a,
        b.skill_name as skill_b,
        count(distinct a.source_job_id) as cooccurrence_count
    from job_skills a
    join job_skills b
        on a.source = b.source
        and a.source_job_id = b.source_job_id
        and a.skill_name < b.skill_name
    group by a.skill_name, b.skill_name
    having count(distinct a.source_job_id) >= 3
)

select
    skill_a,
    skill_b,
    cooccurrence_count,
    current_date as snapshot_date
from pairs
order by cooccurrence_count desc
