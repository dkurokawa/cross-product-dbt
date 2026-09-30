-- Landing quality per delivery partition: what was accepted and what was quarantined.
-- A partition whose files were all quarantined still shows up (files_landed = 0).
with landed as (

    select
        source,
        dataset,
        partition_date,
        sum(files_landed) as files_landed,
        sum(rows_landed) as rows_landed
    from {{ ref('brz_landing_log') }}
    group by source, dataset, partition_date

),

quarantined as (

    select
        source,
        dataset,
        partition_date,
        count(*) as files_quarantined,
        sum(n_violations) as violations
    from {{ ref('brz_quarantine_log') }}
    group by source, dataset, partition_date

)

select
    coalesce(l.source, q.source) as source,
    coalesce(l.dataset, q.dataset) as dataset,
    coalesce(l.partition_date, q.partition_date) as partition_date,
    coalesce(l.files_landed, 0) as files_landed,
    coalesce(l.rows_landed, 0) as rows_landed,
    coalesce(q.files_quarantined, 0) as files_quarantined,
    coalesce(q.violations, 0) as violations
from landed as l
full outer join quarantined as q
    on
        l.source = q.source
        and l.dataset = q.dataset
        and l.partition_date = q.partition_date
