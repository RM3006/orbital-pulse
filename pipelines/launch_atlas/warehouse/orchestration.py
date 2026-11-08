"""Offline Dagster entry point for pinning and preparing warehouse inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dagster import job, op

from launch_atlas.config import Settings
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
