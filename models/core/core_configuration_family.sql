-- Grain: one configuration/family membership per input snapshot.
select *
from {{ ref('stg_bridge_configuration_family') }}
