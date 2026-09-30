-- How well identity matching worked, per source, for the quality report.
-- category = the key method that identified the (live) members: email / phone / phone_kana;
-- `source_local` members could not be matched to anyone (the unmatched count);
-- `deleted_in_source` counts records deleted in their source (C op = D, B logical delete): they
-- contribute nothing downstream and are not in the other categories;
-- `ambiguous_link` (source ALL) counts phone+kana links that resolved more than one e-mail
-- identity and were merged by the documented rule.
select
    source,
    key_method as category,
    count(distinct source_member_id) as n
from {{ ref('int_member_live_rows') }}
group by source, key_method

union all

select
    source,
    'deleted_in_source' as category,
    count(distinct source_member_id) as n
from {{ ref('int_member_source_rows') }}
where is_deleted
group by source

union all

select
    'ALL' as source,
    'ambiguous_link' as category,
    count(*) as n
from {{ ref('int_link_map') }}
where n_email_keys > 1
