-- For every link_key (the key over phone|kana), the e-mail identity it resolves to.
-- Rule (documented, deliberate): the same phone + kana name is the same person, so a
-- link_key resolves to the smallest e-mail member_key seen with it. If more than one distinct
-- e-mail member_key shares a link_key the merge is a judgement call, so it is counted
-- (n_email_keys > 1) and reported in identity_quality instead of being silent.
select
    link_key,
    min(case when key_method = 'email' then member_key end) as email_member_key,
    count(distinct case when key_method = 'email' then member_key end) as n_email_keys
from {{ ref('int_member_source_rows') }}
where link_key is not null
group by link_key
