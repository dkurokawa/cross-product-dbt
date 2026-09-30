-- Latest state of every contract (either generation): the current row of the SCD2 history.
select
    contract_id,
    generation,
    member_id,
    member_key,
    plan_ref,
    plan_key,
    monthly_fee_tax_incl,
    monthly_fee_ex_tax,
    start_date,
    end_date,
    payer_member_id,
    status,
    valid_from as last_changed_at,
    is_deleted
from {{ ref('scd_contract') }}
where is_current and not is_deleted
