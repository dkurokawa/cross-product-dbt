-- Bronze keeps the shape of the source (types, UTC, encoding) and drops no rows, with one
-- exception that is part of what an eventlake table *is*: re-delivered events are one event.
-- Same rule as eventlake's `Lake.events()` (README, section "Reading"): one row per
-- event_id, the copy with the earliest recorded_at; ties broken by the source file path.
with ranked as (

    select
        *,
        row_number() over (
            partition by event_id
            order by recorded_at asc, filename asc
        ) as dedup_rank
    from {{ source('a', 'member_registered') }}

)

select
    event_id,
    occurred_at,
    recorded_at,
    member_id,
    email,
    name,
    name_kana,
    phone,
    birth_date,
    guardian_member_id,
    guardian_email,
    health_notes,
    member_key,
    link_key,
    key_method,
    household_key,
    ingested_at
from ranked
where dedup_rank = 1
