-- D's CSV arrives with Japanese headers (UTF-8 after landing) and text-only columns.
-- Here: English names, real types, ISO dates. A value that does not parse becomes NULL
-- (and is caught by the not_null tests) rather than dropping the row.
select
    {{ col('請求番号') }} as invoice_no,
    {{ parse_date_slash(col('請求日')) }} as invoice_date,
    {{ col('氏名') }} as name,
    {{ col('氏名カナ') }} as name_kana,
    {{ col('電話番号') }} as phone,
    {{ parse_date_slash(col('生年月日')) }} as birth_date,
    {{ col('プランコード') }} as plan_code,
    {{ to_int(col('請求金額（税抜）')) }} as amount_ex_tax,
    {{ to_decimal("replace(" ~ col('消費税率') ~ ", '%', '')") }} / 100 as tax_rate,
    {{ col('入金状況') }} as payment_status,
    member_key,
    link_key,
    key_method,
    household_key,
    cast(dt as date) as file_date,
    {{ to_utc_timestamp('ingested_at') }} as ingested_at
from {{ source('d', 'invoices') }}
