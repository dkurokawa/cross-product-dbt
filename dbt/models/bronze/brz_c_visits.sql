-- Check-ins, CDC log as delivered (insert-only in practice).
select
    op,
    changed_at,
    lsn,
    visit_id,
    member_id,
    checked_in_at,
    facility_id,
    ingested_at
from {{ source('c', 'visits') }}
