-- A product role must see only what its own product collects. Product B never collects a birth
-- date, so its role must not see one (before the fix it received the year from A, C or D).
-- And a product's member table has exactly the live members of that product, no more.
select 'birth_date_year seen by product_b' as problem, count(*) as n
from {{ ref('acc_product_b__dim_member') }}
where birth_date_year is not null
having count(*) > 0

union all

select 'product_b member rows differ from its live source records' as problem, abs(a.n - b.n) as n
from (select count(*) as n from {{ ref('acc_product_b__dim_member') }}) as a
cross join (
    select count(*) as n
    from {{ ref('int_member_live_rows') }}
    where source = 'B'
) as b
where a.n <> b.n

union all

select 'product_a sees members of other products' as problem, count(*) as n
from {{ ref('acc_product_a__dim_member') }} as m
where m.member_key in (
    select r.member_key
    from {{ ref('int_member_live_rows') }} as r
    where r.source <> 'A'
    except
    select r.member_key
    from {{ ref('int_member_live_rows') }} as r
    where r.source = 'A'
)
having count(*) > 0
