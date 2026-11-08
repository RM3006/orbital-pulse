{% test contains_only_requested_snapshot(model, column_name='input_snapshot_id') %}
    select {{ column_name }}
    from {{ model }}
    where {{ column_name }} is null
       or {{ column_name }} != {{ sla_snapshot_value() }}
{% endtest %}
