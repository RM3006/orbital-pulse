from copy import deepcopy
from typing import Any

from launch_atlas.observations import compare_schedules, semantic_hash, semantic_projection


def test_semantic_hash_ignores_payload_media_and_source_counters(
    records: list[dict[str, Any]],
) -> None:
    first = deepcopy(records[2])
    later = deepcopy(first)
    later["rocket"]["payloads"] = [{"id": 999, "name": "changed payload"}]
    later["image"] = "https://example.invalid/new.png"
    later["launch_service_provider"]["total_launch_count"] = 123456
    assert semantic_hash(first) == semantic_hash(later)


def test_semantic_projection_preserves_launch_stages(records: list[dict[str, Any]]) -> None:
    record = deepcopy(records[2])
    projection = semantic_projection(record)
    assert len(projection["stages"]) == len(record["rocket"]["launcher_stage"])
    assert "payloads" not in projection
    assert projection["stages"] == semantic_projection(record)["stages"]


def test_schedule_movement_does_not_infer_delay_across_precision_change() -> None:
    from datetime import UTC, datetime

    result = compare_schedules(
        datetime(2026, 12, 31, tzinfo=UTC),
        "Year",
        datetime(2026, 11, 1, tzinfo=UTC),
        "Month",
    )
    assert result == {"kind": "precision_changed", "difference_seconds": None}
