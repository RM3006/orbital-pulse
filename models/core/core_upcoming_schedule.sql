-- Grain: one supported launch schedule per input snapshot's frozen horizon.
select *
from {{ ref('stg_fact_upcoming_schedule') }}
where precision_supported is true
  and schedule_earliest_utc < horizon_end_exclusive_utc
  and schedule_latest_exclusive_utc > horizon_start_utc
