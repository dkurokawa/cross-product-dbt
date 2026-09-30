-- Maps every (source, source member id) to ONE canonical member_key.
--
-- Products that know a person by e-mail already share a member_key. A product that only
-- knows phone + kana (D) is attached through link_key: the key over phone|kana that the
-- landing step puts on every record where both exist. A person with no e-mail anywhere keeps
-- their own key. The derivation of these keys is in identity.py; this join only follows them.
with link_map as (

    select
        link_key,
        min(case when key_method = 'email' then member_key end) as email_member_key
    from {{ ref('int_member_source_rows') }}
    where link_key is not null
    group by link_key

)

select
    r.source,
    r.source_member_id,
    r.member_key as source_member_key,
    coalesce(l.email_member_key, r.member_key) as member_key
from {{ ref('int_member_source_rows') }} as r
left join link_map as l
    on r.link_key = l.link_key
