-- Product roles must not be able to compare notes: no member or household key value may appear
-- in the tables of two different product roles, and none may equal a canonical (cross-product)
-- key. Keys in product-role tables are product-scoped pseudonyms.
with role_keys as (

    select 'A' as role, member_key as k from {{ ref('acc_product_a__dim_member') }}
    union all select 'A', household_key from {{ ref('acc_product_a__dim_member') }}
    union all select 'A', household_key from {{ ref('acc_product_a__dim_household') }}
    union all select 'A', member_key from {{ ref('acc_product_a__fct_session') }}
    union all select 'A', member_key from {{ ref('acc_product_a__fct_payment') }}
    union all select 'A', member_key from {{ ref('acc_product_a__fct_activity') }}
    union all select 'B', member_key from {{ ref('acc_product_b__dim_member') }}
    union all select 'B', household_key from {{ ref('acc_product_b__dim_member') }}
    union all select 'B', household_key from {{ ref('acc_product_b__dim_household') }}
    union all select 'B', member_key from {{ ref('acc_product_b__fct_booking') }}
    union all select 'B', member_key from {{ ref('acc_product_b__fct_activity') }}
    union all select 'C', member_key from {{ ref('acc_product_c__dim_member') }}
    union all select 'C', household_key from {{ ref('acc_product_c__dim_member') }}
    union all select 'C', household_key from {{ ref('acc_product_c__dim_household') }}
    union all select 'C', member_key from {{ ref('acc_product_c__fct_session') }}
    union all select 'C', member_key from {{ ref('acc_product_c__fct_activity') }}
    union all select 'C', member_key from {{ ref('acc_product_c__scd_contract') }}
    union all select 'D', member_key from {{ ref('acc_product_d__dim_member') }}
    union all select 'D', household_key from {{ ref('acc_product_d__dim_member') }}
    union all select 'D', household_key from {{ ref('acc_product_d__dim_household') }}
    union all select 'D', member_key from {{ ref('acc_product_d__fct_payment') }}

),

canonical as (

    select member_key as k from {{ ref('dim_member') }}
    union all select household_key from {{ ref('dim_member') }}
    union all select link_key from {{ ref('int_member_live_rows') }} where link_key is not null
    union all select source_member_id from {{ ref('int_member_live_rows') }} where source = 'D'

)

select k, 'appears in more than one product role' as problem
from role_keys
group by k
having count(distinct role) > 1

union all

select k, 'equals a canonical key' as problem
from role_keys
where k in (select k from canonical)
