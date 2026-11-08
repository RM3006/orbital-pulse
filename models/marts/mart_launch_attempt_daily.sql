-- One row per snapshot/date/provider/configuration/site combination.
-- Each attempt has one source provider, configuration and site; no stage/family join.
select
    input_snapshot_id,
    date(net_utc) as launch_date_utc,
    extract(year from net_utc) as launch_year_utc,
    provider_id,
    configuration_id,
    site_id,
    count(distinct launch_id) as attempt_count,
    count(distinct if(outcome = 'success', launch_id, null)) as success_count,
    count(distinct if(outcome = 'failure', launch_id, null)) as failure_count,
    count(distinct if(outcome = 'partial_failure', launch_id, null)) as partial_failure_count,
    count(
        distinct if(
            outcome in ('success', 'failure', 'partial_failure'), launch_id, null
        )
    ) as classified_outcome_count,
    count(distinct if(outcome = 'unresolved', launch_id, null)) as unresolved_count
from {{ ref('core_launch_attempt') }}
group by input_snapshot_id, launch_date_utc, launch_year_utc, provider_id, configuration_id, site_id
