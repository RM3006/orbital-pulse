-- Grain: one launch per snapshot's frozen schedule horizon; intervals retain source precision.
select *
from {{ ref('core_upcoming_schedule') }}
