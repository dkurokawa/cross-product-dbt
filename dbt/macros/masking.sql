{#
  Masks for `direct` personal data in the access layer (design decision F4). They are dialect
  macros: the models only ever call `mask_*`, and the masking rule is the same everywhere.
#}

{# E-mail address -> the domain only. #}
{% macro mask_email_domain(expr) %}
    {{ return(adapter.dispatch('mask_email_domain', 'platform')(expr)) }}
{% endmacro %}

{% macro default__mask_email_domain(expr) %}split_part({{ expr }}, '@', 2){% endmacro %}

{% macro bigquery__mask_email_domain(expr) %}split({{ expr }}, '@')[safe_offset(1)]{% endmacro %}

{# Phone number -> its last 4 digits (full-width digits are folded first). #}
{% macro mask_phone_last4(expr) %}
    {{ return(adapter.dispatch('mask_phone_last4', 'platform')(expr)) }}
{% endmacro %}

{% macro default__mask_phone_last4(expr) %}
    right(regexp_replace({{ platform.normalize_digits(expr) }}, '[^0-9]', '', 'g'), 4)
{% endmacro %}

{% macro bigquery__mask_phone_last4(expr) %}
    right(regexp_replace({{ platform.normalize_digits(expr) }}, r'[^0-9]', ''), 4)
{% endmacro %}

{# Birth date -> the year only. #}
{% macro mask_birth_year(expr) %}{{ platform.to_bigint('extract(year from ' ~ expr ~ ')') }}{% endmacro %}

{# A name -> its first character. #}
{% macro mask_initial(expr) %}left({{ expr }}, 1) || '***'{% endmacro %}

{#
  Product-scoped pseudonym of a key (design: product roles never see canonical keys).
  member_key and household_key are already salted HMACs, so hashing them again with the product
  needs no salt in SQL: the result is consistent inside one product (its tables still join) and
  cannot be matched to another product's or to the canonical key.
#}
{% macro pseudonym(expr, product) %}
    {{ return(adapter.dispatch('pseudonym', 'platform')(expr, product)) }}
{% endmacro %}

{% macro default__pseudonym(expr, product) %}sha256(concat('{{ product }}', ':', {{ expr }})){% endmacro %}

{% macro bigquery__pseudonym(expr, product) %}to_hex(sha256(concat('{{ product }}', ':', {{ expr }}))){% endmacro %}
