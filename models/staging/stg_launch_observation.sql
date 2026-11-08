{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'launch_observation') }} as src
where {{ sla_snapshot_filter('src') }}
