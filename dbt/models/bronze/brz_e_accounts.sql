-- The sales sheet after landing has canonical column names, but its cells are still typed
-- by hand: full-width digits, thousands separators, and '—' for "unknown". Placeholders
-- become NULL (never 0), so "no value" cannot be mistaken for "zero seats".
select
    company_name,
    account_key,
    contact_name,
    {{ to_int(normalize_digits('seats')) }} as seats,
    {{ to_int(normalize_digits('monthly_fee_yen')) }} as monthly_fee_yen,
    contract_status,
    sales_owner,
    cast(concat(month, '-01') as date) as report_month,
    {{ to_utc_timestamp('ingested_at') }} as ingested_at
from {{ source('e', 'accounts') }}
