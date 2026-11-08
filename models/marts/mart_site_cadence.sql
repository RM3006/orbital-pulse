-- Grain: one orbital attempt per snapshot, with the prior OBSERVED attempt at that site.
with ordered as (
    select input_snapshot_id, launch_id, site_id, net_utc, interval_basis,
        lag(launch_id) over site as observed_previous_launch_id,
        lag(net_utc) over site as observed_previous_net_utc,
        lag(interval_basis) over site as previous_interval_basis
    from {{ ref('core_launch_attempt') }}
    window site as (partition by input_snapshot_id, site_id order by net_utc, launch_id)
)
select ordered.*, date(net_utc) as launch_date_utc,
    location.name as site_name, location.latitude, location.longitude,
    1 as attempt_count,
    if(ordered.site_id is not null and interval_basis in ('second', 'minute', 'hour')
       and previous_interval_basis in ('second', 'minute', 'hour'),
       timestamp_diff(net_utc, observed_previous_net_utc, second) / 86400.0, null)
       as observed_gap_days,
    {{ 'true' if var('history_complete', false) else 'false' }} as history_complete
from ordered
left join {{ ref('core_dim_launch_site') }} as location
  on ordered.input_snapshot_id = location.input_snapshot_id
 and ordered.site_id = location.site_id
