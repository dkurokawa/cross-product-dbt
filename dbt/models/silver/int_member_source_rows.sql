-- One row per member record per product (the current state of each), with the keys the
-- landing step attached. Identity matching is NOT done here: the rules live in
-- cross_product_platform/identity.py, and this model only carries their output.
with a_members as (

    select
        'A' as source,
        member_id as source_member_id,
        member_key,
        link_key,
        key_method,
        household_key,
        name,
        name_kana,
        email,
        phone,
        birth_date,
        health_notes,
        {{ to_str('null') }} as medical_conditions,
        occurred_at as joined_at,
        false as is_deleted,
        1 as source_priority
    from {{ ref('brz_a_member_registered') }}

),

c_members as (

    select
        'C' as source,
        {{ to_str('member_id') }} as source_member_id,
        member_key,
        link_key,
        key_method,
        household_key,
        name,
        name_kana,
        email,
        phone,
        birth_date,
        {{ to_str('null') }} as health_notes,
        medical_conditions,
        {{ to_utc_from_jst('cast(joined_on as timestamp)') }} as joined_at,
        is_deleted,
        2 as source_priority
    from {{ ref('c_member_current') }}

),

b_ranked as (

    select
        *,
        row_number() over (
            partition by tenant_id, member_no
            order by snapshot_date desc
        ) as recency
    from {{ ref('brz_b_customers') }}

),

b_members as (

    select
        'B' as source,
        tenant_id || '/' || {{ to_str('member_no') }} as source_member_id,
        member_key,
        link_key,
        key_method,
        household_key,
        name,
        name_kana,
        email,
        phone,
        cast(null as date) as birth_date,
        {{ to_str('null') }} as health_notes,
        {{ to_str('null') }} as medical_conditions,
        created_at as joined_at,
        is_deleted,
        3 as source_priority
    from b_ranked
    where recency = 1

),

d_ranked as (

    select
        *,
        row_number() over (
            partition by member_key
            order by invoice_date desc, invoice_no desc
        ) as recency,
        min(invoice_date) over (partition by member_key) as first_invoice_date
    from {{ ref('brz_d_invoices') }}

),

d_members as (

    -- D has no member id: a customer is whoever the phone + kana key says they are.
    select
        'D' as source,
        member_key as source_member_id,
        member_key,
        link_key,
        key_method,
        household_key,
        name,
        name_kana,
        {{ to_str('null') }} as email,
        phone,
        birth_date,
        {{ to_str('null') }} as health_notes,
        {{ to_str('null') }} as medical_conditions,
        {{ to_utc_from_jst('cast(first_invoice_date as timestamp)') }} as joined_at,
        false as is_deleted,
        4 as source_priority
    from d_ranked
    where recency = 1

)

select * from a_members
union all
select * from c_members
union all
select * from b_members
union all
select * from d_members
