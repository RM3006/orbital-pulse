{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'dim_launch_site') }} as src
where {{ sla_snapshot_filter('src') }}
