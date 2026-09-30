-- Distinct source members per key method; `source_local` members could not be matched.
select
    source,
    key_method,
    n_members
from {{ source('meta', 'identity_stats') }}
