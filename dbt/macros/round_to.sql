{#- Postgres has no round(double precision, int), so cast to numeric there.
    Snowflake's bare NUMERIC means NUMBER(38,0), which would drop the decimals,
    so round the float directly instead. -#}
{% macro round_to(expr, places) -%}
    {%- if target.type == 'postgres' -%}
        round(({{ expr }})::numeric, {{ places }})
    {%- else -%}
        round({{ expr }}, {{ places }})
    {%- endif -%}
{%- endmacro %}
