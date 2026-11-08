{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'fact_upcoming_schedule') }} as src
where {{ sla_snapshot_filter('src') }}
