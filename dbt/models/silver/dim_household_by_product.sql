-- The household table a product role gets: households as that product alone knows them (its
-- own records, its own members), with no other product's members or flags.
select
    product,
    household_key,
    count(distinct member_key) as n_members,
    count(distinct member_key) > 1 as is_family,
    min(joined_at) as first_seen_at
from {{ ref('dim_member_by_product') }}
group by product, household_key
