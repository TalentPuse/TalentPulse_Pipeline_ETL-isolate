{{ config(materialized='table') }}

-- Feature store: entity-level features per job (source_job_id).
-- One row per job. Grain: point-in-time = latest snapshot (current_date).
-- Designed for ML consumption: binary skill flags + numeric engagement signals
-- + categorical job/company attributes. Consumed via:
--   SELECT * FROM dbt_dev_feature.job_features
-- Future: PostgresSource in Feast feature_repo when >10k entities.

with jobs as (
    select * from {{ ref('silver_job_detail') }}
),

companies as (
    select company_id, n_active_jobs from {{ ref('silver_company_dim') }}
),

skill_agg as (
    -- Grain must match the entity key below: source_job_id alone would merge the
    -- skills of two unrelated jobs that happen to share an id across boards.
    select
        source,
        source_job_id,
        bool_or(skill_name_norm = 'python')                    as has_python,
        bool_or(skill_name_norm = 'sql')                       as has_sql,
        bool_or(skill_name_norm ~* 'aws|cloud|azure|gcp')      as has_aws_cloud,
        bool_or(skill_name_norm ~* 'ml|ai|llm|machine|deep')   as has_ml_ai,
        bool_or(skill_name_norm ~* 'spark|hadoop|kafka|flink') as has_bigdata,
        bool_or(skill_name_norm ~* 'power bi|tableau|looker|qlik') as has_bi_tool,
        count(*)                                               as n_skills
    from {{ ref('silver_skill_long') }}
    group by 1, 2
)

select
    -- Entity key. `source` belongs here: source_job_id is only unique WITHIN a
    -- source (raw.job_detail is keyed on the pair), so a bare source_job_id lets
    -- two different jobs from two boards collide into one feature row. The
    -- uniqueness test on this table was failing for exactly that reason.
    j.source,
    j.source_job_id,
    current_date                                      as snapshot_date,

    -- Target / salary
    j.salary_vnd_monthly_avg,
    j.is_salary_visible                               as has_salary_visible,

    -- Skill binary flags
    coalesce(s.has_python, false)                     as has_python,
    coalesce(s.has_sql, false)                        as has_sql,
    coalesce(s.has_aws_cloud, false)                  as has_aws_cloud,
    coalesce(s.has_ml_ai, false)                      as has_ml_ai,
    coalesce(s.has_bigdata, false)                    as has_bigdata,
    coalesce(s.has_bi_tool, false)                    as has_bi_tool,
    coalesce(s.n_skills, 0)                           as n_skills,

    -- Job attributes
    (j.job_level in ('Senior', 'Manager', 'Director+'))
                                                      as is_senior,
    (coalesce(j.employment_type, '') ilike '%remote%'
        or j.title ~* 'remote|wfh|work from home')    as is_remote,
    j.job_level,
    j.city_canonical,
    j.region,
    j.degree_label,

    -- Company features
    j.company_size_bucket,
    coalesce(c.n_active_jobs, 1)                      as company_n_active_jobs,

    -- Engagement
    coalesce(j.num_of_views, 0)                       as num_of_views,
    coalesce(j.num_of_applications, 0)                as num_of_applications,
    case when j.posted_at is not null
         then (current_date - j.posted_at::date)
    end                                               as days_since_posted,

    -- Metadata
    j.parsed_at
from jobs j
left join companies c on c.company_id = j.company_id
left join skill_agg s on s.source = j.source and s.source_job_id = j.source_job_id
