-- Corporate accounts (E). One row per company; the latest monthly row wins.
with ranked as (

    select
        *,
        row_number() over (partition by account_key order by report_month desc) as recency,
        min(report_month) over (partition by account_key) as first_month,
        max(report_month) over (partition by account_key) as last_month,
        max(report_month) over () as latest_report_month
    from {{ ref('brz_e_accounts') }}

)

select
    account_key,
    company_name,
    contact_name,
    sales_owner,
    seats,
    monthly_fee_yen,
    first_month,
    last_month,
    case contract_status
        when '契約中' then 'active'
        when '解約' then 'cancelled'
        else 'unknown'
    end as status,
    last_month = latest_report_month
    and contract_status <> '解約' as is_active
from ranked
where recency = 1
