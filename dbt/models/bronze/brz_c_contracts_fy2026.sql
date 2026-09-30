-- FY2026 contract table (second generation: other plan ids, tax-exclusive price), CDC log
-- as delivered.
select
    op,
    changed_at,
    lsn,
    contract_id,
    member_id,
    plan_id,
    price_ex_tax,
    tax_rate,
    effective_from,
    effective_to,
    payer_member_id,
    billing_cycle,
    status,
    ingested_at
from {{ source('c', 'contracts_fy2026') }}
