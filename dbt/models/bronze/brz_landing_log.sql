-- Accepted files per source, dataset and partition, as recorded by the landing step.
select
    run_id,
    source,
    dataset,
    partition_date,
    files_landed,
    rows_landed
from {{ source('meta', 'landing_log') }}
