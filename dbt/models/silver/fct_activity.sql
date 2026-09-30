-- Everything that counts as a member being active, from every product: app workouts and
-- gym check-ins (fct_session) and attended bookings (fct_booking). This is the union the
-- shared metric definitions are computed on.
select
    session_id as activity_id,
    member_key,
    started_at as occurred_at,
    session_date_jst as activity_date_jst,
    session_type as activity_type,
    source
from {{ ref('fct_session') }}

union all

select
    booking_id as activity_id,
    member_key,
    class_start_at as occurred_at,
    class_date_jst as activity_date_jst,
    'booking_attended' as activity_type,
    'B' as source
from {{ ref('fct_booking') }}
where
    status = 'attended'
    and not is_deleted
