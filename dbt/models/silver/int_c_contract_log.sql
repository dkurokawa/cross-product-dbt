-- Both generations of C's contract table in one shape. FY2025 stored a tax-inclusive fee,
-- FY2026 stores a tax-exclusive price; both amounts are kept, the missing one derived.
-- The plan code is namespaced by generation so that dim_plan can map every (generation, code)
-- pair to one plan system.
select
    'FY2025' as generation,
    op,
    changed_at,
    lsn,
    contract_id,
    member_id,
    'C25:' || plan_cd as plan_ref,
    monthly_fee as monthly_fee_tax_incl,
    {{ to_bigint('round(monthly_fee / (1 + ' ~ var('consumption_tax_rate') ~ '))') }}
        as monthly_fee_ex_tax,
    start_dt as start_date,
    end_dt as end_date,
    family_group_id as payer_member_id,
    status
from {{ ref('brz_c_contracts_fy2025') }}

union all

select
    'FY2026' as generation,
    op,
    changed_at,
    lsn,
    contract_id,
    member_id,
    'C26:' || plan_id as plan_ref,
    {{ to_bigint('round(price_ex_tax * (1 + tax_rate))') }} as monthly_fee_tax_incl,
    price_ex_tax as monthly_fee_ex_tax,
    effective_from as start_date,
    effective_to as end_date,
    payer_member_id,
    status
from {{ ref('brz_c_contracts_fy2026') }}
