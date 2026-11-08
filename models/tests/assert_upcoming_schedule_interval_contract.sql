select *
from {{ ref('core_upcoming_schedule') }}
where precision_supported is not true
   or schedule_earliest_utc is null
   or schedule_latest_exclusive_utc is null
   or horizon_start_utc is null
   or horizon_end_exclusive_utc is null
   or schedule_earliest_utc >= schedule_latest_exclusive_utc
   or horizon_start_utc >= horizon_end_exclusive_utc
   or schedule_earliest_utc >= horizon_end_exclusive_utc
   or schedule_latest_exclusive_utc <= horizon_start_utc
