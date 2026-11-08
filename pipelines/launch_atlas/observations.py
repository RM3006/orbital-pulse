"""Stable launch-semantic projection and schedule-change comparison."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from launch_atlas.storage.local import digest

HASH_VERSION = "2"
OBSERVATION_KEY_VERSION = "1"


def observation_event_key(acquisition_id: str, record_ordinal: int) -> str:
    """Return a replay-stable key for one accepted source record event."""
    if not acquisition_id or record_ordinal < 0:
        raise ValueError(
            "Observation event identity requires an acquisition and nonnegative ordinal"
        )
    identity = json.dumps(
        ["launch_observation_event", OBSERVATION_KEY_VERSION, acquisition_id, record_ordinal],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"oev{OBSERVATION_KEY_VERSION}_{digest(identity.encode('utf-8'))}"


def semantic_projection(record: dict[str, Any]) -> dict[str, Any]:
    """Project launch-relevant facts, excluding payload detail, media and counters."""
    provider = record.get("launch_service_provider") or {}
    rocket = record.get("rocket") or {}
    configuration = rocket.get("configuration") or {}
    pad = record.get("pad") or {}
    mission = record.get("mission") or {}
    projection: dict[str, Any] = {
        key: record.get(key)
        for key in ("id", "name", "launch_designator", "net", "window_start", "window_end")
    }
    projection.update(
        {
            "status_id": (record.get("status") or {}).get("id"),
            "precision_id": (record.get("net_precision") or {}).get("id"),
            "precision_name": (record.get("net_precision") or {}).get("name"),
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
        }
    )
    stages: list[dict[str, Any]] = []
    for stage in rocket.get("launcher_stage") or []:
        hardware, landing = stage.get("launcher") or {}, stage.get("landing") or {}
        stages.append(
            {
                key: stage.get(key)
                for key in (
                    "id",
                    "reused",
                    "launcher_flight_number",
                    "previous_flight_date",
                    "turn_around_time",
                )
            }
            | {
                "hardware_id": hardware.get("id"),
                "placeholder": hardware.get("is_placeholder"),
                "landing": {key: landing.get(key) for key in ("id", "attempt", "success")},
            }
        )
    projection["stages"] = sorted(stages, key=lambda item: json.dumps(item, sort_keys=True))
    return projection


def semantic_hash(record: dict[str, Any]) -> str:
    body = json.dumps(
        semantic_projection(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return digest(body.encode("utf-8"))


def compare_schedules(
    old_net: datetime | None,
    old_precision: str | None,
    new_net: datetime | None,
    new_precision: str | None,
) -> dict[str, Any]:
    """Describe comparable precision movement without manufacturing delay."""
    if old_net is None or new_net is None or old_net.tzinfo is None or new_net.tzinfo is None:
        return {"kind": "schedule_revision", "difference_seconds": None}
    if old_precision != new_precision:
        return {"kind": "precision_changed", "difference_seconds": None}
    difference = (new_net - old_net).total_seconds()
    return {"kind": "comparable_movement", "difference_seconds": difference}
