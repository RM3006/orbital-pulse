"""Normalize a run, preserving source grains; no warehouse or network dependency."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import polars as pl
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from launch_atlas.storage.local import digest

PARSER_VERSION = "1"
TS = pl.Datetime("us", "UTC")


class SourceStatus(BaseModel):
    id: int
    name: str


class SourceLaunch(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: UUID
    name: str = Field(min_length=1)
    net: AwareDatetime | None
    last_updated: AwareDatetime
    status: SourceStatus


SCHEMAS: dict[str, dict[str, Any]] = {
    "launch_record_current": {
        "launch_id": pl.String,
        "name": pl.String,
        "launch_designator": pl.String,
        "net_utc": TS,
        "source_updated_at_utc": TS,
        "observed_at_utc": TS,
        "net_precision_id": pl.Int64,
        "net_precision_name": pl.String,
        "window_start_utc": TS,
        "window_end_utc": TS,
        "source_status_id": pl.Int64,
        "source_status_name": pl.String,
        "outcome": pl.String,
        "is_orbital_attempt": pl.Boolean,
        "scope_basis": pl.String,
        "scope_classification": pl.String,
        "provider_id": pl.Int64,
        "configuration_id": pl.Int64,
        "pad_id": pl.Int64,
        "site_id": pl.Int64,
        "mission_type": pl.String,
        "orbit_name": pl.String,
        "stage_detail_available": pl.Boolean,
        "structured_payload_count": pl.Int64,
        "payload_completeness": pl.String,
        "record_hash": pl.String,
        "raw_artifact": pl.String,
    },
    "dim_provider": {
        "provider_id": pl.Int64,
        "name": pl.String,
        "source_type": pl.String,
        "country_codes": pl.List(pl.String),
    },
    "dim_vehicle_configuration": {"configuration_id": pl.Int64, "name": pl.String},
    "dim_vehicle_family": {
        "family_id": pl.Int64,
        "name": pl.String,
        "parent_family_id": pl.Int64,
        "parent_field_supplied": pl.Boolean,
    },
    "bridge_configuration_family": {"configuration_id": pl.Int64, "family_id": pl.Int64},
    "dim_launch_site": {
        "site_id": pl.Int64,
        "name": pl.String,
        "latitude": pl.Float64,
        "longitude": pl.Float64,
        "current_country_code": pl.String,
    },
    "dim_pad": {
        "pad_id": pl.Int64,
        "site_id": pl.Int64,
        "name": pl.String,
        "latitude": pl.Float64,
        "longitude": pl.Float64,
    },
    "stage_flight": {
        "launch_id": pl.String,
        "stage_flight_id": pl.Int64,
        "booster_id": pl.Int64,
        "serial_number": pl.String,
        "hardware_is_placeholder": pl.Boolean,
        "source_reused": pl.Boolean,
        "source_flight_number": pl.Int64,
        "previous_flight_utc": TS,
        "source_turnaround": pl.String,
        "landing_id": pl.Int64,
        "landing_attempt": pl.Boolean,
        "landing_success": pl.Boolean,
    },
    "launch_observation": {
        "launch_id": pl.String,
        "record_hash": pl.String,
        "source_updated_at_utc": TS,
        "observed_at_utc": TS,
        "raw_artifact": pl.String,
        "parser_version": pl.String,
    },
}


def timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Naive source timestamp")
    return parsed.astimezone(UTC)


def semantic_hash(record: dict[str, Any]) -> str:
    """Ignore media, source all-time counters and update-time-only changes."""
    provider = record.get("launch_service_provider") or {}
    rocket = record.get("rocket") or {}
    configuration = rocket.get("configuration") or {}
    pad = record.get("pad") or {}
    mission = record.get("mission") or {}
    fields: dict[str, Any] = {
        key: record.get(key)
        for key in ("id", "name", "launch_designator", "net", "window_start", "window_end")
    }
    fields.update(
        {
            "status_id": (record.get("status") or {}).get("id"),
            "precision_id": (record.get("net_precision") or {}).get("id"),
            "provider_id": provider.get("id"),
            "configuration_id": configuration.get("id"),
            "pad_id": pad.get("id"),
            "site_id": (pad.get("location") or {}).get("id"),
            "mission_id": mission.get("id"),
            "mission_type": mission.get("type"),
            "orbit_id": (mission.get("orbit") or {}).get("id"),
            "orbit_name": (mission.get("orbit") or {}).get("name"),
            "stage_detail_available": "launcher_stage" in rocket,
            "stages": [],
            "payloads": rocket.get("payloads"),
        }
    )
    for stage in rocket.get("launcher_stage") or []:
        hardware, landing = stage.get("launcher") or {}, stage.get("landing") or {}
        fields["stages"].append(
            {
                **{
                    key: stage.get(key)
                    for key in (
                        "id",
                        "reused",
                        "launcher_flight_number",
                        "previous_flight_date",
                        "turn_around_time",
                    )
                },
                "hardware_id": hardware.get("id"),
                "placeholder": hardware.get("is_placeholder"),
                "landing": {key: landing.get(key) for key in ("id", "attempt", "success")},
            }
        )
    fields["stages"].sort(key=lambda stage: stage["id"])
    return digest(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode())


def normalize(
    inputs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in SCHEMAS}
    current: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    observations: set[tuple[str, str]] = set()
    issues: list[str] = []
    # Select corrections by source update time, then observation order.
    # Repeated retrieval is not a new event.
    for record, envelope in inputs:
        launch = SourceLaunch.model_validate(record)
        record_id = str(launch.id)
        record_hash = semantic_hash(record)
        if (record_id, record_hash) not in observations:
            rows["launch_observation"].append(
                {
                    "launch_id": record_id,
                    "record_hash": record_hash,
                    "source_updated_at_utc": launch.last_updated,
                    "observed_at_utc": timestamp(envelope["observed_at_utc"]),
                    "raw_artifact": envelope["artifact"],
                    "parser_version": PARSER_VERSION,
                }
            )
            observations.add((record_id, record_hash))
        prior = current.get(record_id)
        rank = (launch.last_updated, timestamp(envelope["observed_at_utc"]))
        if prior:
            prior_rank = (
                SourceLaunch.model_validate(prior[0]).last_updated,
                timestamp(prior[1]["observed_at_utc"]),
            )
            if rank < prior_rank:
                continue
        current[record_id] = (record, envelope)

    # Deterministic dimension conflict precedence: latest source-updated launch record wins.
    selected = sorted(current.values(), key=lambda pair: (pair[0]["last_updated"], pair[0]["id"]))
    for record, envelope in selected:
        launch = SourceLaunch.model_validate(record)
        launch_id = str(launch.id)
        provider = record.get("launch_service_provider") or {}
        rocket = record.get("rocket") or {}
        config = rocket.get("configuration") or {}
        pad = record.get("pad") or {}
        site = pad.get("location") or {}
        mission = record.get("mission") or {}
        precision = record.get("net_precision") or {}
        status = launch.status.id
        outcome = {
            3: "success",
            4: "failure",
            7: "partial_failure",
            6: "unresolved",
            9: "unresolved",
        }.get(status, "not_launched" if status in (1, 2, 5, 8) else "unknown")
        observed = timestamp(envelope["observed_at_utc"])
        assert observed is not None
        if outcome == "unknown":
            issues.append(f"Unknown source status {status} for {launch_id}")
        is_launched = status in (3, 4, 6, 7, 9)
        if is_launched and (launch.net is None or launch.net > observed):
            issues.append(f"Launched status has missing/future NET for {launch_id}")
        orbit_name = (mission.get("orbit") or {}).get("name")
        scope = (
            "unknown"
            if not orbit_name or orbit_name.lower() in ("unknown", "n/a")
            else "suborbital"
            if "suborbital" in orbit_name.lower()
            else "orbital"
        )
        attempt = (
            is_launched and launch.net is not None and launch.net <= observed and scope == "orbital"
        )
        rows["launch_record_current"].append(
            {
                "launch_id": launch_id,
                "name": launch.name,
                "launch_designator": record.get("launch_designator"),
                "net_utc": launch.net,
                "source_updated_at_utc": launch.last_updated,
                "observed_at_utc": observed,
                "net_precision_id": precision.get("id"),
                "net_precision_name": precision.get("name"),
                "window_start_utc": timestamp(record.get("window_start")),
                "window_end_utc": timestamp(record.get("window_end")),
                "source_status_id": status,
                "source_status_name": launch.status.name,
                "outcome": outcome,
                "is_orbital_attempt": attempt,
                "scope_basis": "source_orbital_filter_and_non_suborbital_mission_orbit",
                "scope_classification": scope,
                "provider_id": provider.get("id"),
                "configuration_id": config.get("id"),
                "pad_id": pad.get("id"),
                "site_id": site.get("id"),
                "mission_type": mission.get("type"),
                "orbit_name": orbit_name,
                "stage_detail_available": "launcher_stage" in rocket,
                "structured_payload_count": len(rocket["payloads"])
                if isinstance(rocket.get("payloads"), list)
                else None,
                "payload_completeness": "unknown",
                "record_hash": semantic_hash(record),
                "raw_artifact": envelope["artifact"],
            }
        )
        if provider.get("id") is not None:
            rows["dim_provider"].append(
                {
                    "provider_id": provider["id"],
                    "name": provider.get("name"),
                    "source_type": (provider.get("type") or {}).get("name"),
                    "country_codes": [c["alpha_3_code"] for c in provider["country"]]
                    if isinstance(provider.get("country"), list)
                    else None,
                }
            )
        if config.get("id") is not None:
            rows["dim_vehicle_configuration"].append(
                {
                    "configuration_id": config["id"],
                    "name": config.get("full_name") or config.get("name"),
                }
            )
            for family in config.get("families", []):
                rows["dim_vehicle_family"].append(
                    {
                        "family_id": family["id"],
                        "name": family.get("name"),
                        "parent_family_id": (family.get("parent") or {}).get("id"),
                        "parent_field_supplied": "parent" in family,
                    }
                )
                rows["bridge_configuration_family"].append(
                    {
                        "configuration_id": config["id"],
                        "family_id": family["id"],
                    }
                )
        if site.get("id") is not None:
            rows["dim_launch_site"].append(
                {
                    "site_id": site["id"],
                    "name": site.get("name"),
                    "latitude": site.get("latitude"),
                    "longitude": site.get("longitude"),
                    "current_country_code": (site.get("country") or {}).get("alpha_3_code"),
                }
            )
        if pad.get("id") is not None:
            rows["dim_pad"].append(
                {
                    "pad_id": pad["id"],
                    "site_id": site.get("id"),
                    "name": pad.get("name"),
                    "latitude": pad.get("latitude"),
                    "longitude": pad.get("longitude"),
                }
            )
        for stage in rocket.get("launcher_stage") or []:
            hardware, landing = stage.get("launcher") or {}, stage.get("landing") or {}
            rows["stage_flight"].append(
                {
                    "launch_id": launch_id,
                    "stage_flight_id": stage["id"],
                    "booster_id": hardware.get("id"),
                    "serial_number": hardware.get("serial_number"),
                    "hardware_is_placeholder": hardware.get("is_placeholder"),
                    "source_reused": stage.get("reused"),
                    "source_flight_number": stage.get("launcher_flight_number"),
                    "previous_flight_utc": timestamp(stage.get("previous_flight_date")),
                    "source_turnaround": stage.get("turn_around_time"),
                    "landing_id": landing.get("id"),
                    "landing_attempt": landing.get("attempt"),
                    "landing_success": landing.get("success"),
                }
            )

    keys = {
        "launch_record_current": ["launch_id"],
        "dim_provider": ["provider_id"],
        "dim_vehicle_configuration": ["configuration_id"],
        "dim_vehicle_family": ["family_id"],
        "bridge_configuration_family": ["configuration_id", "family_id"],
        "dim_launch_site": ["site_id"],
        "dim_pad": ["pad_id"],
        "launch_observation": ["launch_id", "record_hash"],
        "stage_flight": ["launch_id", "stage_flight_id"],
    }
    tables = {}
    for name, schema in SCHEMAS.items():
        frame = pl.DataFrame(rows[name], schema=schema)
        if name == "stage_flight" and frame.unique(subset=keys[name]).height != frame.height:
            issues.append("Duplicate stage-flight key in a current launch")
        tables[name] = frame.unique(subset=keys[name], keep="last").sort(keys[name])
    for name in ("dim_launch_site", "dim_pad"):
        frame = tables[name]
        invalid = frame.filter(
            (pl.col("latitude").is_not_null() & ~pl.col("latitude").is_between(-90, 90))
            | (pl.col("longitude").is_not_null() & ~pl.col("longitude").is_between(-180, 180))
        )
        if invalid.height:
            issues.append(f"Invalid geographic coordinates in {name}")
    relationships = [
        ("launch_record_current", "provider_id", "dim_provider", "provider_id"),
        (
            "launch_record_current",
            "configuration_id",
            "dim_vehicle_configuration",
            "configuration_id",
        ),
        ("launch_record_current", "pad_id", "dim_pad", "pad_id"),
        ("launch_record_current", "site_id", "dim_launch_site", "site_id"),
        ("stage_flight", "launch_id", "launch_record_current", "launch_id"),
    ]
    for child, child_key, parent, parent_key in relationships:
        missing = (
            tables[child]
            .filter(pl.col(child_key).is_not_null())
            .join(
                tables[parent].select(parent_key),
                left_on=child_key,
                right_on=parent_key,
                how="anti",
            )
        )
        if missing.height:
            issues.append(f"Orphan relationship {child}.{child_key} -> {parent}.{parent_key}")
    launches = tables["launch_record_current"]
    tables["fact_launch_attempt"] = launches.filter(pl.col("is_orbital_attempt"))
    attempts = tables["fact_launch_attempt"]
    classified = attempts.filter(pl.col("outcome").is_in(["success", "failure", "partial_failure"]))
    counts = {
        outcome: attempts.filter(pl.col("outcome") == outcome).height
        for outcome in ("success", "failure", "partial_failure", "unresolved")
    }
    return tables, {
        "passed": not issues,
        "issues": issues,
        "parser_version": PARSER_VERSION,
        "rows": {name: frame.height for name, frame in tables.items()},
        "sample_metrics": {
            "orbital_attempts": attempts.height,
            "classified_outcomes": classified.height,
            **counts,
            "success_share": counts["success"] / classified.height if classified.height else None,
        },
        "coverage": {
            "missing_provider": launches.filter(pl.col("provider_id").is_null()).height,
            "missing_precision": launches.filter(pl.col("net_precision_id").is_null()).height,
            "unknown_orbital_scope": launches.filter(
                pl.col("scope_classification") == "unknown"
            ).height,
            "explicit_suborbital_scope": launches.filter(
                pl.col("scope_classification") == "suborbital"
            ).height,
            "with_stage_detail": launches.filter(pl.col("stage_detail_available")).height,
            "payload_completeness": (
                "unknown; structured counts do not establish actual payload totals"
            ),
        },
    }
