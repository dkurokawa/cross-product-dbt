-- One row per booking per delivered snapshot; nothing is dropped. Times are naive Tokyo time
-- in B and become UTC here; prices stay tax-exclusive (the tax-inclusive price is derived in
-- silver, next to the original).
select
    tenant_id,
    booking_no,
    member_no,
    {{ to_utc_from_jst('class_start_at') }} as class_start_at,
    {{ to_utc_from_jst('created_at') }} as created_at,
    status,
    price_ex_tax,
    tax_rate,
    is_deleted,
    cast(dt as date) as snapshot_date,
    ingested_at
from {{ source('b', 'bookings') }}
