-- The CDC log exactly as delivered: one row per change, every op kept (I / U / D).
-- Latest state and history are built in silver (c_member_current).
select
    op,
    changed_at,
    lsn,
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
    ingested_at
from {{ source('c', 'members') }}
