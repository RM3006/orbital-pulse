"""Immutable local Parquet generations derived from a committed SQLite revision."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import polars as pl

from launch_atlas.config import Settings
from launch_atlas.normalize import SCHEMAS, normalize
from launch_atlas.storage.local import LocalStore, atomic_json, digest
from launch_atlas.storage.state import StateStore
from launch_atlas.time_precision import schedule_interval

UPCOMING_SCHEMA = {
    "launch_id": pl.String,
    "as_of_utc": pl.Datetime("us", "UTC"),
    "horizon_start_utc": pl.Datetime("us", "UTC"),
    "horizon_end_exclusive_utc": pl.Datetime("us", "UTC"),
    "net_utc": pl.Datetime("us", "UTC"),
    "schedule_earliest_utc": pl.Datetime("us", "UTC"),
    "schedule_latest_exclusive_utc": pl.Datetime("us", "UTC"),
    "interval_basis": pl.String,
    "precision_supported": pl.Boolean,
    "source_status_id": pl.Int64,
    "source_status_name": pl.String,
    "scope_classification": pl.String,
    "net_precision_id": pl.Int64,
    "net_precision_name": pl.String,
    "raw_artifact": pl.String,
}


def export_current(settings: Settings) -> dict[str, Any]:
    """Build and validate a complete generation before replacing the active pointer."""
    local = LocalStore(settings.data_dir)
    with local.lock():
        state = StateStore(settings.data_dir)
        revision = state.revision()
        inputs = state.current_records()
        if not inputs:
            raise ValueError("Cannot export persistent state before a nonempty acquisition")
        tables, quality = normalize(inputs)
        if not quality["passed"]:
            raise ValueError("Persistent current state failed normalization/quality checks")
        tables["launch_observation"] = pl.DataFrame(
            state.observation_records(), schema=SCHEMAS["launch_observation"]
        ).sort(["launch_id", "observed_at_utc", "record_hash"])
        quality["rows"]["launch_observation"] = tables["launch_observation"].height
        as_of = state.latest_complete_refresh_asof() or datetime.now(UTC)
        horizon_end = as_of + timedelta(days=90)
        current_by_id = {
            row["launch_id"]: row for row in tables["launch_record_current"].iter_rows(named=True)
        }
        upcoming_rows = []
        eligible_statuses = {1, 2, 5, 8}
        unsupported_horizon_count = 0
        for record, envelope in inputs:
            launch_id = str(record["id"])
            current = current_by_id[launch_id]
            if current["source_status_id"] not in eligible_statuses:
                continue
            interval = schedule_interval(current["net_utc"], record.get("net_precision"))
            if not interval.precision_supported:
                unsupported_horizon_count += 1
                continue
            if interval.overlaps(as_of, horizon_end):
                upcoming_rows.append(
                    {
                        "launch_id": launch_id,
                        "as_of_utc": as_of,
                        "horizon_start_utc": as_of,
                        "horizon_end_exclusive_utc": horizon_end,
                        "net_utc": current["net_utc"],
                        "schedule_earliest_utc": interval.earliest_utc,
                        "schedule_latest_exclusive_utc": interval.latest_exclusive_utc,
                        "interval_basis": interval.interval_basis,
                        "precision_supported": interval.precision_supported,
                        "source_status_id": current["source_status_id"],
                        "source_status_name": current["source_status_name"],
                        "scope_classification": current["scope_classification"],
                        "net_precision_id": current["net_precision_id"],
                        "net_precision_name": current["net_precision_name"],
                        "raw_artifact": envelope["artifact"],
                    }
                )
        tables["fact_upcoming_schedule"] = (
            pl.DataFrame(upcoming_rows, schema=UPCOMING_SCHEMA)
            .unique(subset=["launch_id"], keep="last")
            .sort("launch_id")
        )
        quality["rows"]["fact_upcoming_schedule"] = tables["fact_upcoming_schedule"].height
        quality["coverage"]["upcoming_unsupported_precision"] = unsupported_horizon_count
        quality["coverage"]["upcoming_horizon_days"] = 90
        quality["coverage"]["upcoming_as_of_utc"] = as_of.isoformat()
        quality["state_revision"] = revision
        quality["generation_created_at_utc"] = datetime.now(UTC).isoformat()

        exports = local.root / "exports"
        generations = exports / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex[:12]
        generation_id = f"revision-{revision:08d}-{token}"
        staging = generations / f".staging-{generation_id}"
        final = generations / generation_id
        staging.mkdir()
        files: dict[str, dict[str, Any]] = {}
        for name, frame in tables.items():
            path = staging / f"{name}.parquet"
            frame.write_parquet(path)
            body = path.read_bytes()
            files[path.name] = {"sha256": digest(body), "rows": frame.height}
        atomic_json(staging / "quality.json", quality)
        manifest = {
            "generation_id": generation_id,
            "state_revision": revision,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "database": "state/launch_atlas.sqlite",
            "files": files,
            "quality_passed": True,
            "rows": quality["rows"],
            "coverage": quality["coverage"],
        }
        atomic_json(staging / "manifest.json", manifest)
        # Verify every written Parquet checksum before selecting the generation.
        for filename, metadata in files.items():
            if digest((staging / filename).read_bytes()) != metadata["sha256"]:
                raise OSError(f"Export checksum mismatch for {filename}")
        staging.replace(final)
        atomic_json(exports / "active.json", manifest)
        return manifest
