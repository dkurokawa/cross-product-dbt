-- Every contract has exactly one open-ended version, and consecutive versions touch:
-- valid_to of one version is valid_from of the next, with no overlap and no gap.
-- (dbt_utils.mutually_exclusive_ranges needs a non-null upper bound, which an SCD2 current
-- row by definition does not have.)
with versions as (

    select
        contract_id,
        valid_from,
        valid_to,
        lead(valid_from) over (partition by contract_id order by valid_from, valid_to) as next_valid_from
    from {{ ref('scd_contract') }}

),

open_ended as (

    select contract_id
    from versions
    group by contract_id
    having sum(case when valid_to is null then 1 else 0 end) <> 1

),

broken_chain as (

    select contract_id
    from versions
    where valid_to is not null
        and (valid_to <> next_valid_from or valid_from > valid_to)

)

select contract_id from open_ended
union all
select contract_id from broken_chain
