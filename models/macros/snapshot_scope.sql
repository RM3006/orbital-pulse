{% macro sla_snapshot_value() -%}
    {%- if execute -%}
        {%- set snapshot_id = var('input_snapshot_id', '') -%}
        {%- if modules.re.fullmatch('sla-input-v1-[a-f0-9]{64}', snapshot_id) is none -%}
            {{ exceptions.raise_compiler_error(
                'Pass a valid input_snapshot_id using --vars; model builds must be snapshot-pinned.'
            ) }}
        {%- endif -%}
        {{- return("'" ~ snapshot_id ~ "'") -}}
    {%- else -%}
        {{- return("'sla-input-v1-parse-only'") -}}
    {%- endif -%}
{%- endmacro %}

{% macro sla_snapshot_filter(alias='src') -%}
    {{- alias -}}.input_snapshot_id = {{ sla_snapshot_value() }}
{%- endmacro %}
