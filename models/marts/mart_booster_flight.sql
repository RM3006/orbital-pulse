-- Grain: one identifiable hardware ID per qualifying orbital launch and snapshot.
-- Collapse repeated stage associations before reuse counts or hardware sequencing.
with flights as (
    select
        input_snapshot_id, launch_id, booster_id,
        min(launch_net_utc) as launch_net_utc,
        any_value(launch_interval_basis) as launch_interval_basis,
        any_value(configuration_id) as configuration_id,
        any_value(serial_number) as serial_number,
        count(*) as source_association_count,
        count(distinct source_reused) <= 1
          and count(distinct source_flight_number) <= 1 as source_fields_consistent,
        any_value(source_reused) as source_reused,
        any_value(source_flight_number) as source_flight_number,
        count(distinct previous_flight_utc) <= 1 as previous_time_consistent,
        any_value(previous_flight_utc) as source_previous_flight_utc
    from {{ ref('mart_stage_flight') }}
    where booster_id is not null and hardware_is_placeholder is false
    group by input_snapshot_id, launch_id, booster_id
), ordered as (
    select *,
        lag(launch_net_utc) over hardware as observed_previous_flight_utc,
        lag(source_flight_number) over hardware as observed_previous_flight_number,
        lag(source_fields_consistent) over hardware as previous_fields_consistent
        , lag(launch_interval_basis) over hardware as previous_interval_basis
    from flights
    window hardware as (
        partition by input_snapshot_id, booster_id order by launch_net_utc, launch_id
    )
), flags as (
    select *,
        source_fields_consistent and source_reused is not null
          and (source_flight_number is null or (
            source_flight_number >= 1
            and source_reused = (source_flight_number > 1)
          )) as reuse_known,
        coalesce(
            source_fields_consistent and previous_fields_consistent and previous_time_consistent
            and source_flight_number = observed_previous_flight_number + 1
            and observed_previous_flight_number >= 1
            and launch_interval_basis in ('second', 'minute')
            and previous_interval_basis in ('second', 'minute')
            and launch_net_utc > observed_previous_flight_utc
            and (source_previous_flight_utc is null
                 or source_previous_flight_utc = observed_previous_flight_utc), false
        ) as turnaround_supported
    from ordered
)
select *,
    1 as identifiable_flight_count,
    if(reuse_known, 1, 0) as reuse_known_count,
    if(reuse_known and source_reused, 1, 0) as reused_flight_count,
    if(turnaround_supported,
       timestamp_diff(launch_net_utc, observed_previous_flight_utc, second) / 86400.0,
       null) as turnaround_days,
    if(turnaround_supported, 'consecutive_source_flight_numbers', 'insufficient_or_conflicting_history')
       as turnaround_basis
from flags
