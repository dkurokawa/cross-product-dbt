-- The member table a product role gets: one row per (product, live member), built from that
-- product's OWN source record only. Nothing is coalesced from another product, so a product sees
-- exactly the attributes it collects itself (product B has no birth date, so it shows none), and
-- there are no cross-product flags or counts. The canonical member_key is the same hash as in
-- dim_member; it says nothing about the other products.
select
    r.source as product,
    x.member_key,
    r.source_member_id,
    r.household_key,
    r.key_method,
    r.name,
    r.name_kana,
    r.email,
    r.phone,
    r.birth_date,
    r.health_notes,
    r.medical_conditions,
    r.joined_at
from {{ ref('int_member_live_rows') }} as r
inner join {{ ref('xref_member') }} as x
    on
        r.source = x.source
        and r.source_member_id = x.source_member_id
