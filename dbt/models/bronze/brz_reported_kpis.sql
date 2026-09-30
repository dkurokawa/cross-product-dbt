-- What each product reports about itself, as delivered.
select
    product,
    reported_as,
    cast(period_month as date) as period_month,
    value,
    definition
from {{ source('meta', 'reported_kpis') }}
