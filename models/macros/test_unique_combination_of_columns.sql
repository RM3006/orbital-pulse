{% test unique_combination_of_columns(model, combination_of_columns) %}
    {% set grouped_columns = combination_of_columns | join(', ') %}
    select {{ grouped_columns }}
    from {{ model }}
    group by {{ grouped_columns }}
    having count(*) > 1
{% endtest %}
