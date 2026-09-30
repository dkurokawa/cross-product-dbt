-- Same rule as eventlake's `Lake.events()` (README, section "Reading"): one row per
-- event_id, the copy with the earliest recorded_at; ties broken by the source file path.
with ranked as (

    select
        *,
        row_number() over (
            partition by event_id
            order by recorded_at asc, filename asc
        ) as dedup_rank
    from {{ source('a', 'workout_completed') }}

)

select
    event_id,
    occurred_at,
    recorded_at,
    member_id,
    workout_type,
    duration_min,
    ingested_at
from ranked
where dedup_rank = 1
