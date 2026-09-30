-- A household is the payer plus the people who use the service (rule in household.py:
-- a guardian's e-mail ties a child to the payer's household in every product).
select
    household_key,
    count(*) as n_members,
    count(*) > 1 as is_family,
    max(case when in_a then 1 else 0 end) = 1 as in_a,
    max(case when in_b then 1 else 0 end) = 1 as in_b,
    max(case when in_c then 1 else 0 end) = 1 as in_c,
    max(case when in_d then 1 else 0 end) = 1 as in_d,
    min(first_seen_at) as first_seen_at
from {{ ref('dim_member') }}
group by household_key
