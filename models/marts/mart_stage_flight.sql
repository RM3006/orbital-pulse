-- Grain: one source stage-flight association; launch measures must not be summed from this mart.
select
    stage.input_snapshot_id,
    stage.launch_id,
    stage.stage_flight_id,
    launch.net_utc as launch_net_utc,
    launch.configuration_id,
    stage.booster_id,
    stage.serial_number,
    stage.hardware_is_placeholder,
    stage.source_reused,
    stage.source_flight_number,
    stage.previous_flight_utc,
    stage.source_turnaround,
    stage.landing_id,
    stage.landing_attempt,
    stage.landing_success
from {{ ref('core_stage_flight') }} as stage
left join {{ ref('core_launch_current') }} as launch
  on stage.input_snapshot_id = launch.input_snapshot_id
 and stage.launch_id = launch.launch_id
