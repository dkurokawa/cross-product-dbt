-- A person with no live source record (every record deleted in its source) must not be in
-- dim_member: a record deleted in its source contributes nothing downstream.
select m.member_key
from {{ ref('dim_member') }} as m
where not exists (
    select 1
    from {{ ref('int_member_source_rows') }} as r
    inner join {{ ref('xref_member') }} as x
        on
            r.source = x.source
            and r.source_member_id = x.source_member_id
    where
        x.member_key = m.member_key
        and not r.is_deleted
)
