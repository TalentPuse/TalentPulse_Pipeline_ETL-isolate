{{ config(materialized='view') }}

-- Bronze staging: pass-through view over raw.job_detail.
-- Filters out inactive postings; downstream silver will normalize.
select *
from {{ source('raw', 'job_detail') }}
where coalesce(is_active, true)
