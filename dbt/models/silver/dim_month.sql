-- One row per month of the period the platform covers, from a real month spine (not from the
-- months in which something happened): a month with no activity is still a month.
select
    {{ month_start('date_month') }} as period_month,
    {{ month_end_date('date_month') }} as month_end
from (
    {{ month_spine(
        "(select " ~ month_start('min(period_start)') ~ " from " ~ ref('brz_period') ~ ")",
        "(select " ~ month_start('max(period_end)') ~ " from " ~ ref('brz_period') ~ ")"
    ) }}
) as spine
