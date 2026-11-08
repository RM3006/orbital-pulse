with expected as (
    select
        input_snapshot_id,
        date(net_utc) as launch_date_utc,
        provider_id,
        configuration_id,
        site_id,
        count(distinct launch_id) as attempt_count
    from {{ ref('core_launch_attempt') }}
    group by input_snapshot_id, launch_date_utc, provider_id, configuration_id, site_id
), actual as (
    select
        input_snapshot_id,
        launch_date_utc,
        provider_id,
        configuration_id,
        site_id,
        sum(attempt_count) as attempt_count
    from {{ ref('mart_launch_attempt_daily') }}
    group by input_snapshot_id, launch_date_utc, provider_id, configuration_id, site_id
)
select expected.*
from expected
full outer join actual
  on expected.input_snapshot_id = actual.input_snapshot_id
 and expected.launch_date_utc = actual.launch_date_utc
 and expected.provider_id is not distinct from actual.provider_id
 and expected.configuration_id is not distinct from actual.configuration_id
 and expected.site_id is not distinct from actual.site_id
where expected.attempt_count is distinct from actual.attempt_count
