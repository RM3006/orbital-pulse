select *
from {{ ref('core_launch_attempt') }}
where is_orbital_attempt is not true
   or source_status_id is null
   or source_status_id not in (3, 4, 6, 7, 9)
   or net_utc is null
   or observed_at_utc is null
   or net_utc > observed_at_utc
   or outcome is null
   or outcome not in ('success', 'failure', 'partial_failure', 'unresolved')
