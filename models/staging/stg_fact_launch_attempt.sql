{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'fact_launch_attempt') }} as src
where {{ sla_snapshot_filter('src') }}
