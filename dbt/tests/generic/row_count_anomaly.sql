{#
  row_count_anomaly(window=28, z=3): warns when the daily row count of a model is more than
  `z` standard deviations away from the mean of the previous `window` days that have data.

  It also reports a day that is missing altogether (a gap in the daily series is a count of
  zero, which is what a broken upstream delivery looks like). Returns one row per anomaly.
#}
{% test row_count_anomaly(model, date_column, window=28, z=3) %}

{{ config(severity='warn') }}

with daily as (

    select
        cast({{ date_column }} as date) as d,
        count(*) as n
    from {{ model }}
    group by 1

),

stats as (

    select
        d,
        n,
        avg(n) over (order by d rows between {{ window - 1 }} preceding and current row) as mean_n,
        stddev(n) over (order by d rows between {{ window - 1 }} preceding and current row) as sd_n,
        count(n) over (order by d rows between {{ window - 1 }} preceding and current row) as obs_n
    from daily

),

previous as (

    select
        d,
        n,
        lag(d) over (order by d) as prev_d,
        lag(mean_n) over (order by d) as prev_mean,
        lag(sd_n) over (order by d) as prev_sd,
        lag(obs_n) over (order by d) as prev_obs
    from stats

),

anomalies as (

    -- a day whose count is far from what the previous window led us to expect
    select
        d as day,
        n as observed,
        prev_mean as expected,
        'unusual_count' as kind
    from previous
    where prev_obs >= {{ window }}
        and prev_sd > 0
        and abs(n - prev_mean) / prev_sd > {{ z }}

    union all

    -- the day right after the previous one is missing: expected but zero rows
    select
        cast({{ dbt.dateadd('day', 1, 'prev_d') }} as date) as day,
        0 as observed,
        prev_mean as expected,
        'missing_day' as kind
    from previous
    where prev_obs >= {{ window }}
        and prev_sd > 0
        and {{ dbt.datediff('prev_d', 'd', 'day') }} > 1
        and prev_mean / prev_sd > {{ z }}

)

select * from anomalies

{% endtest %}
