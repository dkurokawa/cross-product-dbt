-- How well identity matching worked, per source, for the quality report.
-- category = the key method that identified the members (email / phone / phone_kana);
-- `source_local` members could not be matched to anyone (the unmatched count);
-- `ambiguous_link` (source ALL) counts phone+kana links that resolved more than one e-mail
-- identity and were merged by the documented rule.
select
    source,
    key_method as category,
    n_members as n
from {{ ref('brz_identity_stats') }}

union all

select
    'ALL' as source,
    'ambiguous_link' as category,
    count(*) as n
from {{ ref('int_link_map') }}
where n_email_keys > 1
