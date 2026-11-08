{% macro generate_alias_name(custom_alias_name=none, node=none) -%}
    {%- set prefix = var('candidate_prefix', '') -%}
    {%- if modules.re.fullmatch('[a-z][a-z0-9_]{0,63}', prefix) is none and prefix != '' -%}
        {{ exceptions.raise_compiler_error('Invalid candidate table prefix') }}
    {%- endif -%}
    {{- prefix ~ (custom_alias_name or node.name) -}}
{%- endmacro %}
