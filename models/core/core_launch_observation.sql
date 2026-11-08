-- Grain: one semantic transition event; A-to-B-to-A remains three events.
select *
from {{ ref('stg_launch_observation') }}
