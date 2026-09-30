-- Files moved to quarantine by the landing step (never silently dropped).
select
    source,
    dataset,
    file,
    partition_date,
    stage,
    n_violations,
    reason,
    quarantined_at
from {{ source('meta', 'quarantine_log') }}
