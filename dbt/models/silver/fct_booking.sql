-- One row per booking, state as of the latest B snapshot. B writes tax-exclusive prices;
-- the tax-inclusive price is derived and both are kept.
with ranked as (

    select
        *,
        row_number() over (
            partition by tenant_id, booking_no
            order by snapshot_date desc
        ) as recency
    from {{ ref('brz_b_bookings') }}

)

select
    r.tenant_id || '/' || {{ to_str('r.booking_no') }} as booking_id,
    r.tenant_id,
    x.member_key,
    r.created_at as booked_at,
    r.class_start_at,
    {{ to_date_jst('r.class_start_at') }} as class_date_jst,
    r.status,
    r.price_ex_tax,
    {{ to_bigint('round(r.price_ex_tax * (1 + r.tax_rate))') }} as price_tax_incl,
    r.tax_rate,
    r.is_deleted,
    r.snapshot_date as as_of_snapshot
from ranked as r
inner join {{ ref('xref_member') }} as x
    on
        x.source = 'B'
        and x.source_member_id = r.tenant_id || '/' || {{ to_str('r.member_no') }}
where r.recency = 1
