-- Grain: one launch/stage-flight association per input snapshot.
select *
from {{ ref('stg_stage_flight') }}
