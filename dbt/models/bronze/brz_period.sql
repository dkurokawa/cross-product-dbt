-- The days the landed data covers, one row per landing run (the platform knows its own period;
-- silver builds the month spine from it, not from the months that happen to have activity).
select
    run_id,
    cast(period_start as date) as period_start,
    cast(period_end as date) as period_end
from {{ source('meta', 'period') }}
