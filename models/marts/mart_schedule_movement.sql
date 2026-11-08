-- Grain: one accepted semantic observation event, including non-schedule changes.
-- No schedule is reconstructed before this project's first observation.
with ordered as (
    select *,
        lag(observation_event_key) over observed as previous_event_key,
        lag(schedule_earliest_utc) over observed as previous_schedule_earliest_utc,
        lag(net_precision_id) over observed as previous_precision_id,
        lag(interval_basis) over observed as previous_interval_basis,
        lag(precision_supported) over observed as previous_precision_supported
    from {{ ref('core_launch_observation') }}
    window observed as (
        partition by input_snapshot_id, launch_id order by observation_sequence
    )
), classified as (
    select *, case
        when previous_event_key is null then 'first_observation'
        when not coalesce(precision_supported and previous_precision_supported, false)
          then 'unsupported_precision'
        when net_precision_id != previous_precision_id or interval_basis != previous_interval_basis
          then 'precision_changed'
        when interval_basis not in ('second', 'minute', 'hour', 'day') then 'coarse_precision'
        else 'comparable' end as comparison_basis
    from ordered
)
select *, comparison_basis = 'comparable' as is_comparable,
    if(comparison_basis = 'comparable',
       timestamp_diff(schedule_earliest_utc, previous_schedule_earliest_utc, second), null)
       as schedule_movement_seconds
from classified
