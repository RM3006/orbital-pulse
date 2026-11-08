-- Grain: one qualifying orbital attempt per launch per input snapshot.
select *
from {{ ref('stg_fact_launch_attempt') }}
where is_orbital_attempt is true
  and source_status_id in (3, 4, 6, 7, 9)
  and net_utc is not null
  and net_utc <= observed_at_utc
