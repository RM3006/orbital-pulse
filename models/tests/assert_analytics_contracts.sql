-- All branches return only contract violations, including join-volume inflation.
select 'reuse_denominator' as violation
from {{ ref('mart_booster_flight') }}
where reused_flight_count > reuse_known_count or reuse_known_count > identifiable_flight_count
   or turnaround_days < 0 or (not turnaround_supported and turnaround_days is not null)
union all
select 'landing_denominator'
from {{ ref('mart_landing') }}
where successful_landing_count > known_landing_outcome_count
   or known_landing_outcome_count + unknown_landing_outcome_count > attempted_landing_count
union all
select 'cadence_volume'
from (select 1 as singleton)
where (select count(*) from {{ ref('mart_site_cadence') }})
   != (select count(*) from {{ ref('core_launch_attempt') }})
union all
select 'cadence_negative' from {{ ref('mart_site_cadence') }} where observed_gap_days < 0
union all
select 'schedule_volume'
from (select 1 as singleton)
where (select count(*) from {{ ref('mart_schedule_movement') }})
   != (select count(*) from {{ ref('core_launch_observation') }})
union all
select 'schedule_precision'
from {{ ref('mart_schedule_movement') }}
where not is_comparable and schedule_movement_seconds is not null
union all
select 'stage_coverage'
from {{ ref('mart_data_coverage') }}
where attempts_with_recorded_stage_flights > orbital_attempt_records
   or attempts_with_stage_field_supplied > orbital_attempt_records
