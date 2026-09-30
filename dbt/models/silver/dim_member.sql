-- One row per real person across products. Attributes come from the highest-priority
-- product that knows the person (A, then C, B, D); the flags say where they appear.
-- Contains personal data (name, contact, birth date) and sensitive free text; access to it
-- is governed downstream, not here.
with rows_with_key as (

    select
        x.member_key,
        r.source,
        r.name,
        r.name_kana,
        r.email,
        r.phone,
        r.birth_date,
        r.health_notes,
        r.medical_conditions,
        r.household_key,
        r.key_method,
        r.joined_at,
        r.source_priority
    from {{ ref('int_member_live_rows') }} as r
    inner join {{ ref('xref_member') }} as x
        on
            r.source = x.source
            and r.source_member_id = x.source_member_id

),

ranked as (

    select
        *,
        row_number() over (
            partition by member_key
            order by source_priority asc, joined_at asc
        ) as priority_rank
    from rows_with_key

),

aggregated as (

    select
        member_key,
        max(case when source = 'A' then 1 else 0 end) as in_a,
        max(case when source = 'B' then 1 else 0 end) as in_b,
        max(case when source = 'C' then 1 else 0 end) as in_c,
        max(case when source = 'D' then 1 else 0 end) as in_d,
        min(joined_at) as first_seen_at,
        max(health_notes) as health_notes,
        max(medical_conditions) as medical_conditions
    from rows_with_key
    group by member_key

)

select
    p.member_key,
    p.household_key,
    p.key_method,
    p.name,
    p.name_kana,
    p.email,
    p.phone,
    p.birth_date,
    g.health_notes,
    g.medical_conditions,
    g.first_seen_at,
    g.in_a = 1 as in_a,
    g.in_b = 1 as in_b,
    g.in_c = 1 as in_c,
    g.in_d = 1 as in_d,
    g.in_a + g.in_b + g.in_c + g.in_d as n_products
from ranked as p
inner join aggregated as g
    on p.member_key = g.member_key
where p.priority_rank = 1
