{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'dim_vehicle_configuration') }} as src
where {{ sla_snapshot_filter('src') }}
