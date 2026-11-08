{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {%- if node.name.startswith('stg_') -%}
            {{- env_var('DBT_STAGING_DATASET', 'space_launch_atlas_staging') -}}
        {%- elif node.name.startswith('core_') -%}
            {{- env_var('DBT_CORE_DATASET', 'space_launch_atlas_core') -}}
        {%- elif node.name.startswith('mart_') -%}
            {{- env_var('DBT_MARTS_DATASET', 'space_launch_atlas_marts') -}}
        {%- else -%}
            {{- target.schema -}}
        {%- endif -%}
    {%- else -%}
        {{- custom_schema_name | trim -}}
    {%- endif -%}
{%- endmacro %}
