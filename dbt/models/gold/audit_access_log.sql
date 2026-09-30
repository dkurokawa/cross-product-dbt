{{ config(tags=['audit']) }}

-- Who asked what, and what came of it: one row per gateway call. "Who read a sensitive
-- column" is a query on this table:
--   select principal, role, occurred_at, sensitive_columns from audit_access_log
--   where touched_sensitive;
-- The principal is self-declared (the local gateway has no authentication); on BigQuery the IAM
-- principal in INFORMATION_SCHEMA.JOBS is the trustworthy counterpart.
select
    event_id,
    occurred_at,
    'executed' as outcome,
    principal,
    role,
    sql_hash,
    sql_normalized,
    {{ list_to_text('referenced_tables') }} as referenced_tables,
    {{ list_to_text('referenced_columns') }} as referenced_columns,
    {{ list_to_text('sensitive_columns') }} as sensitive_columns,
    touched_sensitive,
    row_count,
    {{ to_str('null') }} as deny_reason
from {{ ref('brz_audit_query_executed') }}

union all

select
    event_id,
    occurred_at,
    'denied' as outcome,
    principal,
    role,
    sql_hash,
    sql_normalized,
    {{ list_to_text('referenced_tables') }} as referenced_tables,
    {{ list_to_text('referenced_columns') }} as referenced_columns,
    {{ list_to_text('sensitive_columns') }} as sensitive_columns,
    touched_sensitive,
    {{ to_bigint('null') }} as row_count,
    deny_reason
from {{ ref('brz_audit_query_denied') }}
