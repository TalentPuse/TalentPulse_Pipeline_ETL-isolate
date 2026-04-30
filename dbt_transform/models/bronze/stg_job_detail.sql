{{ config(materialized='view') }}

select
    *,
    case
        when expired_at is not null and expired_at < current_timestamp
        then true
        else false
    end as is_expired_calc,
    case
        when expired_at is not null and expired_at < current_timestamp
        then false
        else true
    end as is_active_calc
from {{ source('raw', 'job_detail') }}
