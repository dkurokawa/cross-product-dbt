-- What C erased (op = D) must not survive in dim_member: no sensitive text, and no name or
-- e-mail taken from the deleted record when no live record backs the person.
select m.member_key
from {{ ref('dim_member') }} as m
inner join {{ ref('c_member_current') }} as d
    on
        m.member_key = d.member_key
        and d.is_deleted
where
    m.medical_conditions = d.medical_conditions
    or (
        m.email = d.email
        and m.name = d.name
        and not exists (
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
    )
