-- The member records that are still alive in their source. A record deleted in its source
-- (C: op = D, B: logical delete, latest snapshot) contributes nothing downstream: no attributes,
-- no product flag, no sensitive text, no identity. int_member_source_rows keeps the deleted ones
-- only so that identity_quality can count them.
select *
from {{ ref('int_member_source_rows') }}
where not is_deleted
