select *
from {{ ref('stg_dim_provider') }}
