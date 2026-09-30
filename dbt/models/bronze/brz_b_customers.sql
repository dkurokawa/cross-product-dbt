-- One row per customer per delivered snapshot; nothing is dropped. B writes naive Tokyo
-- time, converted to UTC here. (tenant_id, member_no) is B's identity: member_no is a running
-- number inside the tenant, so it is only unique together with tenant_id.
select
    tenant_id,
    member_no,
    name,
    name_kana,
    email,
    phone,
    {{ to_utc_from_jst('created_at') }} as created_at,
    is_deleted,
    {{ to_utc_from_jst('deleted_at') }} as deleted_at,
    cast(dt as date) as snapshot_date,
    member_key,
    link_key,
    key_method,
    household_key,
    ingested_at
from {{ source('b', 'customers') }}
