{{ config(materialized='view') }}

select *
from {{ source('launch_atlas', 'launch_record_current') }} as src
where {{ sla_snapshot_filter('src') }}
