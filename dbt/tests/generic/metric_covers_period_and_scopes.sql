{#
  metric_covers_period_and_scopes(products): the metric has one row for every month of the period
  (dim_month) and every product scope, with 0 where nothing happened. Returns the missing
  (month, product) pairs; a metric that only has rows for months or scopes with activity fails.
#}
{% test metric_covers_period_and_scopes(model, products) %}

with grid as (

    select
        m.period_month,
        p.product
    from {{ ref('dim_month') }} as m
    cross join (
        {% for product in products %}
        select '{{ product }}' as product
        {% if not loop.last %}union all{% endif %}
        {% endfor %}
    ) as p

)

select
    g.period_month,
    g.product
from grid as g
left join {{ model }} as t
    on
        g.period_month = t.period_month
        and g.product = t.product
where t.period_month is null

{% endtest %}
