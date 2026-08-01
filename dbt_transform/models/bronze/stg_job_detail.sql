{{ config(materialized='view') }}

-- Bronze: light type/derivation layer over raw.job_detail.
--
-- Expiry is derived here, and it is not as simple as `expired_at < now()`.
-- LinkedIn does not publish an expiry date at all: measured on prod 2026-08-01,
-- 2693 of 2693 LinkedIn rows had expired_at IS NULL. With only the date check,
-- `is_expired_calc` was false for every one of them, so LinkedIn postings stayed
-- "currently hiring" forever — 552 of them were more than 30 days old and 68 more
-- than 180 days. LinkedIn is ~61% of the warehouse, so that is the single most
-- user-visible piece of staleness on the site.
--
-- Fallback: age a posting out `job_stale_days` after posted_at when the source
-- gives us nothing better. Sources that DO publish an expiry (VietnamWorks,
-- ITviec, TopCV) are unaffected — their date still wins.

with base as (

    select
        *,
        case
            when expired_at is not null
                then expired_at < current_timestamp
            when posted_at is not null
                then posted_at < current_timestamp
                     - interval '{{ var("job_stale_days", 45) }} days'
            -- No expiry and no posting date: nothing to reason from. Leaving it
            -- active is the lesser error — dropping a job we simply know little
            -- about would silently shrink the board.
            else false
        end as is_expired_calc
    from {{ source('raw', 'job_detail') }}

)

select
    *,
    not is_expired_calc as is_active_calc
from base
