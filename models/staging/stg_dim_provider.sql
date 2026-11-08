{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'dim_provider') }} as src
where {{ sla_snapshot_filter('src') }}
