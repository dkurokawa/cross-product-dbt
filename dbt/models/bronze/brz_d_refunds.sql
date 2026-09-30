-- Refunds come in their own files; they refer to an invoice by number.
select
    {{ col('返金番号') }} as refund_no,
    {{ col('元請求番号') }} as invoice_no,
    {{ parse_date_slash(col('返金日')) }} as refund_date,
    {{ to_int(col('返金額（税抜）')) }} as refund_amount_ex_tax,
    cast(dt as date) as file_date,
    {{ to_utc_timestamp('ingested_at') }} as ingested_at
from {{ source('d', 'refunds') }}
