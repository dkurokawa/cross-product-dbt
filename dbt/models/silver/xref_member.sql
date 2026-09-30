-- Maps every (source, source member id) to ONE canonical member_key.
--
-- Products that know a person by e-mail already share a member_key. A product that only
-- knows phone + kana (D) is attached through link_key: the key over phone|kana that the
-- landing step puts on every record where both exist. A person with no e-mail anywhere keeps
-- their own key. The derivation of these keys is in identity.py; this join only follows them.
-- link_ambiguous marks records whose link_key resolved several e-mail identities (see
-- int_link_map): they are merged by the documented rule and counted in identity_quality.
select
    r.source,
    r.source_member_id,
    r.member_key as source_member_key,
    coalesce(l.email_member_key, r.member_key) as member_key,
    coalesce(l.n_email_keys, 0) > 1 as link_ambiguous
from {{ ref('int_member_source_rows') }} as r
left join {{ ref('int_link_map') }} as l
    on r.link_key = l.link_key
