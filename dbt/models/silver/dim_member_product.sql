-- Which products each person is a customer of, one row per (member, product).
-- Used to scope a metric to "the people of product X".
select
    member_key,
    'A' as product
from {{ ref('dim_member') }}
where in_a

union all

select
    member_key,
    'B' as product
from {{ ref('dim_member') }}
where in_b

union all

select
    member_key,
    'C' as product
from {{ ref('dim_member') }}
where in_c

union all

select
    member_key,
    'D' as product
from {{ ref('dim_member') }}
where in_d
