{#
  Dialect-specific SQL lives ONLY in this file (design section 6). Models call these
  macros; each one dispatches on the adapter. Adding a warehouse means adding one
  `<adapter>__<name>` implementation per macro here, nothing in the models.
#}

{# Quoted identifier (the acquired billing system's headers are Japanese). #}
{% macro col(name) %}{{ adapter.quote(name) }}{% endmacro %}

{# --- time ---------------------------------------------------------------------- #}

{# A naive timestamp written in Tokyo local time -> an absolute (UTC) timestamp. #}
{% macro to_utc_from_jst(expr) %}
    {{ return(adapter.dispatch('to_utc_from_jst', 'platform')(expr)) }}
{% endmacro %}

{% macro default__to_utc_from_jst(expr) %}
    {{ exceptions.raise_compiler_error("to_utc_from_jst is not implemented for this adapter") }}
{% endmacro %}

{% macro duckdb__to_utc_from_jst(expr) %}(({{ expr }}) at time zone 'Asia/Tokyo'){% endmacro %}

{% macro bigquery__to_utc_from_jst(expr) %}timestamp({{ expr }}, 'Asia/Tokyo'){% endmacro %}

{# An absolute timestamp -> the calendar date in Tokyo. #}
{% macro to_date_jst(expr) %}
    {{ return(adapter.dispatch('to_date_jst', 'platform')(expr)) }}
{% endmacro %}

{% macro default__to_date_jst(expr) %}
    {{ exceptions.raise_compiler_error("to_date_jst is not implemented for this adapter") }}
{% endmacro %}

{% macro duckdb__to_date_jst(expr) %}cast(timezone('Asia/Tokyo', {{ expr }}) as date){% endmacro %}

{% macro bigquery__to_date_jst(expr) %}date({{ expr }}, 'Asia/Tokyo'){% endmacro %}

{# 'YYYY/MM/DD' text -> date (NULL when the text does not parse). #}
{% macro parse_date_slash(expr) %}
    {{ return(adapter.dispatch('parse_date_slash', 'platform')(expr)) }}
{% endmacro %}

{% macro default__parse_date_slash(expr) %}
    {{ exceptions.raise_compiler_error("parse_date_slash is not implemented for this adapter") }}
{% endmacro %}

{% macro duckdb__parse_date_slash(expr) %}cast(try_strptime({{ expr }}, '%Y/%m/%d') as date){% endmacro %}

{% macro bigquery__parse_date_slash(expr) %}safe.parse_date('%Y/%m/%d', {{ expr }}){% endmacro %}

{# ISO text with an offset ('2026-09-30 11:36:08+00:00') -> an absolute timestamp. #}
{% macro to_utc_timestamp(expr) %}
    {{ return(adapter.dispatch('to_utc_timestamp', 'platform')(expr)) }}
{% endmacro %}

{% macro default__to_utc_timestamp(expr) %}
    {{ exceptions.raise_compiler_error("to_utc_timestamp is not implemented for this adapter") }}
{% endmacro %}

{% macro duckdb__to_utc_timestamp(expr) %}cast({{ expr }} as timestamptz){% endmacro %}

{% macro bigquery__to_utc_timestamp(expr) %}timestamp({{ expr }}){% endmacro %}

{# --- numbers ------------------------------------------------------------------- #}

{# Full-width digits -> ASCII, thousands separators dropped (sheet typing habits). #}
{% macro normalize_digits(expr) %}
    {{ return(adapter.dispatch('normalize_digits', 'platform')(expr)) }}
{% endmacro %}

{% macro default__normalize_digits(expr) %}translate({{ expr }}, '０１２３４５６７８９，,', '0123456789'){% endmacro %}

{# Cast that yields NULL instead of an error when the text does not parse. #}
{% macro try_cast(expr, type_name) %}
    {{ return(adapter.dispatch('try_cast', 'platform')(expr, type_name)) }}
{% endmacro %}

{% macro default__try_cast(expr, type_name) %}{{ dbt.safe_cast(expr, type_name) }}{% endmacro %}

{% macro duckdb__try_cast(expr, type_name) %}try_cast({{ expr }} as {{ type_name }}){% endmacro %}

{# Text -> bigint, NULL when it is not a number ('—' placeholders stay NULL, never 0). #}
{% macro to_int(expr) %}{{ platform.try_cast(expr, dbt.type_bigint()) }}{% endmacro %}

{# Text -> exact decimal, NULL when it is not a number. #}
{% macro to_decimal(expr) %}{{ platform.try_cast(expr, dbt.type_numeric()) }}{% endmacro %}

{# --- keys ---------------------------------------------------------------------- #}

{# Stable hash of several columns (NULL-safe); used to detect a real change in SCD2. #}
{% macro row_hash(columns) %}{{ dbt_utils.generate_surrogate_key(columns) }}{% endmacro %}

{# --- types --------------------------------------------------------------------- #}

{% macro to_str(expr) %}cast({{ expr }} as {{ dbt.type_string() }}){% endmacro %}

{% macro to_bigint(expr) %}cast({{ expr }} as {{ dbt.type_bigint() }}){% endmacro %}

{% macro to_numeric(expr) %}cast({{ expr }} as {{ dbt.type_numeric() }}){% endmacro %}

{# --- calendar ------------------------------------------------------------------ #}

{# First day of the month of a date. #}
{% macro month_start(expr) %}cast({{ dbt.date_trunc('month', expr) }} as date){% endmacro %}

{# Last day of the month of a date. #}
{% macro month_end_date(expr) %}
    cast({{ dbt.dateadd('day', -1, dbt.dateadd('month', 1, dbt.date_trunc('month', expr))) }} as date)
{% endmacro %}

{# A date moved by n days. #}
{% macro add_days(expr, n) %}cast({{ dbt.dateadd('day', n, expr) }} as date){% endmacro %}

{# A list column as comma-separated text. #}
{% macro list_to_text(expr) %}array_to_string({{ expr }}, ','){% endmacro %}

{#
  Gateway audit events (eventlake). They exist only after the gateway has answered a query, so a
  fresh clone has none. On DuckDB, when no event file exists yet, the audit models read an
  empty relation with the right column types instead of failing; nothing is invented. Other
  warehouses read the source table as usual.
#}
{% macro audit_events(name) %}
    {{ return(adapter.dispatch('audit_events', 'platform')(name)) }}
{% endmacro %}

{% macro default__audit_events(name) %}{{ source('audit', name) }}{% endmacro %}

{% macro duckdb__audit_events(name) %}
    {%- set has_events = false -%}
    {#- a unit test supplies its own rows for the source -#}
    {%- if execute and model.resource_type != 'unit_test' -%}
        {%- set root = env_var('PLATFORM_AUDIT_ROOT', 'build/audit') -%}
        {%- set found = run_query("select count(*) from glob('" ~ root ~ "/" ~ name ~ "/dt=*/part-*.parquet')") -%}
        {%- set has_events = found.columns[0].values()[0] > 0 -%}
    {%- endif -%}
    {%- if has_events or not execute or model.resource_type == 'unit_test' -%}
        {{ source('audit', name) }}
    {%- else -%}
        (
            select
                cast(null as varchar) as event_id,
                cast(null as timestamptz) as occurred_at,
                cast(null as timestamptz) as recorded_at,
                cast(null as varchar) as filename,
                cast(null as varchar) as principal,
                cast(null as varchar) as role,
                cast(null as varchar) as sql_hash,
                cast(null as varchar) as sql_normalized,
                cast(null as varchar[]) as referenced_tables,
                cast(null as varchar[]) as referenced_columns,
                cast(null as varchar[]) as sensitive_columns,
                cast(null as boolean) as touched_sensitive,
                cast(null as bigint) as row_count,
                cast(null as varchar) as deny_reason
            where false
        )
    {%- endif -%}
{% endmacro %}
