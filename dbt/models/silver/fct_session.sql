-- A session is one time a member trained: an app workout (A) or a check-in at a gym (C).
with c_visits as (

    select
        *,
        row_number() over (partition by visit_id order by changed_at desc, lsn desc) as recency
    from {{ ref('brz_c_visits') }}

),

unioned as (

    select
        w.event_id as session_id,
        'A' as source,
        'workout' as session_type,
        x.member_key,
        w.occurred_at as started_at,
        w.duration_min,
        {{ to_str('null') }} as facility_id
    from {{ ref('brz_a_workout_completed') }} as w
    inner join {{ ref('xref_member') }} as x
        on
            x.source = 'A'
            and w.member_id = x.source_member_id

    union all

    select
        'C-' || {{ to_str('v.visit_id') }} as session_id,
        'C' as source,
        'visit' as session_type,
        x.member_key,
        v.checked_in_at as started_at,
        {{ to_bigint('null') }} as duration_min,
        v.facility_id
    from c_visits as v
    inner join {{ ref('xref_member') }} as x
        on
            x.source = 'C'
            and x.source_member_id = {{ to_str('v.member_id') }}
    where
        v.recency = 1
        and v.op <> 'D'

)

select
    session_id,
    source,
    session_type,
    member_key,
    started_at,
    {{ to_date_jst('started_at') }} as session_date_jst,
    duration_min,
    facility_id
from unioned
