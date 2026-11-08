-- Grain: one current source record per launch per input snapshot.
select *
from {{ ref('stg_launch_record_current') }}
