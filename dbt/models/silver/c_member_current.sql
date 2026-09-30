-- Latest state of every C member: the log ordered by (changed_at, lsn), last one wins.
-- A member whose last operation is D (an erasure) is kept with is_deleted = true so downstream
-- models can exclude it explicitly instead of it silently vanishing.
with ranked as (

    select
        *,
        row_number() over (
            partition by member_id
            order by changed_at desc, lsn desc
        ) as recency
    from {{ ref('brz_c_members') }}

)

select
    member_id,
    name,
    name_kana,
    email,
    phone,
    birth_date,
    guardian_email,
    medical_conditions,
    facility_id,
    status,
    joined_on,
    member_key,
    link_key,
    key_method,
    household_key,
    changed_at as last_changed_at,
    op = 'D' as is_deleted
from ranked
where recency = 1
