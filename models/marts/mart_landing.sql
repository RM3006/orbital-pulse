-- Grain: one source landing ID per snapshot, regardless of repeated stage associations.
with landings as (
    select input_snapshot_id, landing_id,
        any_value(launch_id) as launch_id,
        min(launch_net_utc) as launch_net_utc,
        any_value(configuration_id) as configuration_id,
        count(*) as source_association_count,
        count(distinct launch_id) = 1
          and count(distinct landing_attempt) <= 1
          and count(distinct landing_success) <= 1 as source_fields_consistent,
        any_value(landing_attempt) as landing_attempt,
        any_value(landing_success) as landing_success
    from {{ ref('mart_stage_flight') }}
    where landing_id is not null
    group by input_snapshot_id, landing_id
)
select *,
    if(source_fields_consistent and landing_attempt is true, 1, 0) as attempted_landing_count,
    if(source_fields_consistent and landing_attempt is true and landing_success is not null,
       1, 0) as known_landing_outcome_count,
    if(source_fields_consistent and landing_attempt is true and landing_success is true,
       1, 0) as successful_landing_count,
    if(source_fields_consistent and landing_attempt is true and landing_success is null,
       1, 0) as unknown_landing_outcome_count
from landings
