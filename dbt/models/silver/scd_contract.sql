-- Contract history (SCD2) built from the CDC log of both table generations.
-- * a log record that changes none of the tracked columns (C often re-sends the same row) is
--   not a new version;
-- * valid_to is the next version's valid_from (NULL for the current version);
-- * a delete (op = D) is its own version with is_deleted = true;
-- * a record that follows a delete always starts a new version, even if every tracked
--   column equals the deleted one (a re-insert must not be swallowed by the hash comparison).
with log as (

    select
        c.*,
        {{ row_hash(['plan_ref', 'monthly_fee_tax_incl', 'monthly_fee_ex_tax', 'start_date',
                     'end_date', 'payer_member_id', 'status']) }} as attribute_hash
    from {{ ref('int_c_contract_log') }} as c

),

compared as (

    select
        *,
        lag(attribute_hash)
            over (partition by contract_id order by changed_at, lsn)
            as previous_hash,
        lag(op)
            over (partition by contract_id order by changed_at, lsn)
            as previous_op
    from log

),

versions as (

    select
        *,
        changed_at as valid_from,
        lead(changed_at) over (partition by contract_id order by changed_at, lsn) as valid_to
    from compared
    where
        previous_hash is null
        or attribute_hash <> previous_hash
        or op = 'D'
        or previous_op = 'D'

)

select
    v.contract_id,
    v.generation,
    v.member_id,
    x.member_key,
    v.plan_ref,
    p.plan_key,
    v.monthly_fee_tax_incl,
    v.monthly_fee_ex_tax,
    v.start_date,
    v.end_date,
    v.payer_member_id,
    v.status,
    v.valid_from,
    v.valid_to,
    v.valid_to is null as is_current,
    v.op = 'D' as is_deleted
from versions as v
left join {{ ref('dim_plan') }} as p
    on v.plan_ref = p.plan_ref
inner join {{ ref('xref_member') }} as x
    on
        x.source = 'C'
        and x.source_member_id = {{ to_str('v.member_id') }}
