-- FY2025 contract table (first generation), CDC log as delivered.
select
    op,
    changed_at,
    lsn,
    contract_id,
    member_id,
    plan_cd,
    monthly_fee,
    start_dt,
    end_dt,
    family_group_id,
    status,
    ingested_at
from {{ source('c', 'contracts_fy2025') }}
