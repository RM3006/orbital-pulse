-- Family memberships can overlap. Counts are distinct within family, not additive across families.
select
    launch.input_snapshot_id,
    date(launch.net_utc) as launch_date_utc,
    extract(year from launch.net_utc) as launch_year_utc,
    family.family_id,
    count(distinct launch.launch_id) as attempt_count,
    count(distinct if(launch.outcome = 'success', launch.launch_id, null)) as success_count,
    count(distinct if(launch.outcome = 'failure', launch.launch_id, null)) as failure_count,
    count(
        distinct if(launch.outcome = 'partial_failure', launch.launch_id, null)
    ) as partial_failure_count,
    count(
        distinct if(
            launch.outcome in ('success', 'failure', 'partial_failure'),
            launch.launch_id,
            null
        )
    ) as classified_outcome_count,
    count(distinct if(launch.outcome = 'unresolved', launch.launch_id, null)) as unresolved_count
from {{ ref('core_launch_attempt') }} as launch
left join {{ ref('core_configuration_family') }} as family
  on launch.input_snapshot_id = family.input_snapshot_id
 and launch.configuration_id = family.configuration_id
group by launch.input_snapshot_id, launch_date_utc, launch_year_utc, family.family_id
