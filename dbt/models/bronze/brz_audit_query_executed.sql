{{ config(tags=['audit']) }}

-- Gateway audit events, one row per event. Same rule as eventlake's `Lake.events()` (README,
-- section "Reading"): one row per event_id, the copy with the earliest recorded_at; ties broken
-- by the source file path.
with ranked as (

    select
        *,
        row_number() over (
            partition by event_id
            order by recorded_at asc, filename asc
        ) as dedup_rank
    from {{ source('audit', 'query_executed') }}

)

select
    event_id,
    occurred_at,
    principal,
    role,
    sql_hash,
    sql_normalized,
    referenced_tables,
    referenced_columns,
    sensitive_columns,
    touched_sensitive,
    row_count
from ranked
where dedup_rank = 1
