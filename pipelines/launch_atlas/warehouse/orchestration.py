"""Offline Dagster entry point for pinning and preparing warehouse inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dagster import job, op

from launch_atlas.config import Settings
from launch_atlas.storage.local import atomic_json
from launch_atlas.storage.snapshots import create_input_snapshot
from launch_atlas.warehouse.loading import prepare_load_plan


@op(config_schema={"data_dir": str})
def pin_warehouse_inputs(context) -> dict[str, str]:
    """Verify current local state and Parquet, then return the immutable snapshot ID."""
    data_dir = Path(context.op_config["data_dir"])
    snapshot = create_input_snapshot(Settings(data_dir=data_dir))
    return {"data_dir": str(data_dir), "snapshot_id": snapshot["input_snapshot_id"]}


@op
def prepare_warehouse_plan(pinned_inputs: dict[str, str]) -> dict[str, Any]:
    """Write only the local load plan; cloud adapters are not constructed here."""
    plan = prepare_load_plan(Path(pinned_inputs["data_dir"]), pinned_inputs["snapshot_id"])
    return plan.summary()


@job
def local_warehouse_prepare_job():
    """Credential-free input snapshot → deterministic placeholder-safe load plan."""
    prepare_warehouse_plan(pin_warehouse_inputs())


@op(required_resource_keys={"pipeline"})
def acquire_accepted_inputs(context) -> dict[str, Any]:
    return context.resources.pipeline.acquire()


@op(required_resource_keys={"pipeline"})
def pin_reporting_inputs(context, accepted: dict[str, Any]) -> Any:
    return context.resources.pipeline.pin(accepted)


@op(required_resource_keys={"pipeline"})
def load_reporting_inputs(context, plan: Any) -> dict[str, Any]:
    return context.resources.pipeline.load(plan)


@op(required_resource_keys={"pipeline"})
def build_and_validate_models(context, loaded: dict[str, Any]) -> dict[str, Any]:
    return context.resources.pipeline.build(loaded)


@op(required_resource_keys={"pipeline"})
def retain_reporting_candidate(context, candidate: dict[str, Any]) -> dict[str, Any]:
    return context.resources.pipeline.retain(candidate)


@op(required_resource_keys={"pipeline"})
def activate_reporting_release(context, retained: dict[str, Any]) -> dict[str, Any]:
    return context.resources.pipeline.activate(retained)


@job
def warehouse_release_job():
    """One dependency chain; any failed op prevents later writes/activation."""
    activate_reporting_release(
        retain_reporting_candidate(
            build_and_validate_models(
                load_reporting_inputs(pin_reporting_inputs(acquire_accepted_inputs()))
            )
        )
    )


def execute_warehouse_pipeline(pipeline: Any):
    """Execute in process and retain a credential-free failure summary."""
    result = warehouse_release_job.execute_in_process(
        resources={"pipeline": pipeline}, raise_on_error=False
    )
    if not result.success:
        atomic_json(
            pipeline.run_dir / "failure.json",
            {
                "status": "failed",
                "dagster_run_id": result.run_id,
                "failed_steps": [event.step_key for event in result.get_step_failure_events()],
            },
        )
    return result
