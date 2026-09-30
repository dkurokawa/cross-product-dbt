-- Money movements from A (in-app purchases, tax-inclusive) and D (invoices and refunds,
-- tax-exclusive), unified to a tax-inclusive amount. The amount as the source stated it is
-- kept in amount_original / amount_original_basis; refunds are negative.
with a_charges as (

    select
        p.event_id as payment_id,
        'A' as source,
        'charge' as payment_type,
        x.member_key,
        {{ to_str('null') }} as plan_ref,
        p.occurred_at,
        {{ to_bigint('round(p.amount_tax_incl / (1 + p.tax_rate))') }} as amount_ex_tax,
        p.amount_tax_incl,
        p.amount_tax_incl as amount_original,
        'tax_incl' as amount_original_basis,
        p.tax_rate
    from {{ ref('brz_a_purchase_made') }} as p
    left join {{ ref('xref_member') }} as x
        on
            x.source = 'A'
            and p.member_id = x.source_member_id

),

d_charges as (

    select
        i.invoice_no as payment_id,
        'D' as source,
        'charge' as payment_type,
        x.member_key,
        'D:' || i.plan_code as plan_ref,
        {{ to_utc_from_jst('i.invoice_date') }} as occurred_at,
        i.amount_ex_tax,
        {{ to_bigint('round(i.amount_ex_tax * (1 + i.tax_rate))') }} as amount_tax_incl,
        i.amount_ex_tax as amount_original,
        'ex_tax' as amount_original_basis,
        i.tax_rate
    from {{ ref('brz_d_invoices') }} as i
    left join {{ ref('xref_member') }} as x
        on
            x.source = 'D'
            and i.member_key = x.source_member_id

),

d_refunds as (

    select
        r.refund_no as payment_id,
        'D' as source,
        'refund' as payment_type,
        x.member_key,
        'D:' || i.plan_code as plan_ref,
        {{ to_utc_from_jst('r.refund_date') }} as occurred_at,
        -r.refund_amount_ex_tax as amount_ex_tax,
        -{{ to_bigint('round(r.refund_amount_ex_tax * (1 + i.tax_rate))') }} as amount_tax_incl,
        -r.refund_amount_ex_tax as amount_original,
        'ex_tax' as amount_original_basis,
        i.tax_rate
    from {{ ref('brz_d_refunds') }} as r
    left join {{ ref('brz_d_invoices') }} as i
        on r.invoice_no = i.invoice_no
    left join {{ ref('xref_member') }} as x
        on
            x.source = 'D'
            and i.member_key = x.source_member_id

)

select * from a_charges
union all
select * from d_charges
union all
select * from d_refunds
