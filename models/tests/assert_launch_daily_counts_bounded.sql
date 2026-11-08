select *
from {{ ref('mart_launch_attempt_daily') }}
where classified_outcome_count != success_count + failure_count + partial_failure_count
   or classified_outcome_count > attempt_count
   or unresolved_count > attempt_count
