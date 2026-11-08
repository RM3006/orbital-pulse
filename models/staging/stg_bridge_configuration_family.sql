{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'bridge_configuration_family') }} as src
where {{ sla_snapshot_filter('src') }}
