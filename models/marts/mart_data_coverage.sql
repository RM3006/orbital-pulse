-- Grain: one snapshot. Independent coverage counts; never a composite quality score.
with stages as (
    select input_snapshot_id, count(distinct launch_id) as attempts_with_recorded_stage_flights
    from {{ ref('mart_stage_flight') }} group by input_snapshot_id
)
select launches.input_snapshot_id,
    count(*) as current_launch_records,
    countif(is_orbital_attempt) as orbital_attempt_records,
    countif(is_orbital_attempt and stage_detail_available) as attempts_with_stage_field_supplied,
    coalesce(max(stages.attempts_with_recorded_stage_flights), 0)
        as attempts_with_recorded_stage_flights,
    countif(scope_classification = 'unknown') as unknown_scope_records,
    countif(is_orbital_attempt and outcome = 'unresolved') as unresolved_attempt_outcomes,
    countif(source_status_id in (1, 2, 5, 8) and not precision_supported)
        as unsupported_upcoming_precision_records,
    max(source_updated_at_utc) as latest_source_update_utc,
    max(observed_at_utc) as latest_observation_utc,
    {% if var('source_check_as_of_utc', none) is not none %}
    timestamp('{{ var("source_check_as_of_utc") }}') as last_complete_refresh_utc,
    {% else %}
    cast(null as timestamp) as last_complete_refresh_utc,
    {% endif %}
    {{ 'true' if var('history_complete', false) else 'false' }} as history_complete
from {{ ref('core_launch_current') }} as launches
left join stages on launches.input_snapshot_id = stages.input_snapshot_id
group by launches.input_snapshot_id
