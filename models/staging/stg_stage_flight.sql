{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'stage_flight') }} as src
where {{ sla_snapshot_filter('src') }}
