import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl
import pytest
from launch_atlas.normalize import normalize


def inputs(records: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (r, {"observed_at_utc": "2026-11-01T17:00:00Z", "artifact": "fixture.json"})
        for r in records
    ]


def synthetic(record: dict[str, Any], identifier: int, status: int) -> dict[str, Any]:
    result = deepcopy(record)
    result.update(id=str(UUID(int=identifier)), name=f"SYNTHETIC EDGE CASE {identifier}")
    result["status"] = {"id": status, "name": "Synthetic test status"}
    return result


def test_real_nonempty_slice_and_known_hardware(records: list[dict[str, Any]]) -> None:
    tables, quality = normalize(inputs(records))
    assert quality["passed"]
    assert tables["launch_record_current"].height == 6
    assert tables["fact_launch_attempt"].height == 5
    assert tables["dim_provider"].height >= 3
    hardware = tables["stage_flight"].filter(pl.col("serial_number") == "B1093").row(0, named=True)
    assert hardware["source_flight_number"] == 18
    assert hardware["source_reused"] is True
    assert hardware["landing_attempt"] is True
    assert hardware["landing_success"] is True
    sp = (
        tables["launch_record_current"]
        .filter(pl.col("name").str.contains("15-18"))
        .row(0, named=True)
    )
    assert sp["structured_payload_count"] == 0
    assert sp["payload_completeness"] == "unknown"


def test_denominator_exclusions_and_multiple_stages(records: list[dict[str, Any]]) -> None:
    base = records[2]
    cases = [
        synthetic(base, index, status)
        for index, status in enumerate([3, 4, 7, 6, 9, 1, 2, 5, 8], 1)
    ]
    suborbital = synthetic(base, 20, 3)
    suborbital["mission"]["orbit"]["name"] = "Suborbital"
    cases.append(suborbital)
    for case in cases:
        stage = deepcopy(case["rocket"]["launcher_stage"][0])
        stage["id"] = 99999
        case["rocket"]["launcher_stage"].append(stage)
    tables, quality = normalize(inputs(cases))
    assert quality["passed"]
    assert tables["stage_flight"].height == 20
    assert quality["sample_metrics"] == {
        "orbital_attempts": 5,
        "classified_outcomes": 3,
        "success": 1,
        "failure": 1,
        "partial_failure": 1,
        "unresolved": 2,
        "success_share": pytest.approx(1 / 3),
    }
    assert tables["fact_launch_attempt"]["launch_id"].n_unique() == 5


def test_missing_values_are_not_failures_or_zero(records: list[dict[str, Any]]) -> None:
    record = synthetic(records[2], 50, 3)
    record.update(
        launch_service_provider=None, pad=None, rocket=None, mission=None, net_precision=None
    )
    tables, quality = normalize(inputs([record]))
    row = tables["launch_record_current"].row(0, named=True)
    assert row["provider_id"] is None
    assert row["structured_payload_count"] is None
    assert row["net_precision_name"] is None
    assert row["stage_detail_available"] is False
    assert quality["coverage"]["missing_provider"] == 1
    assert quality["coverage"]["unknown_orbital_scope"] == 1
    assert tables["fact_launch_attempt"].is_empty()
    assert tables["stage_flight"].is_empty()


@pytest.mark.parametrize("precision", ["Day", "Month", "Quarter", "Year"])
def test_coarse_dates_remain_coarse(records: list[dict[str, Any]], precision: str) -> None:
    record = synthetic(records[-1], 60, 2)
    record["net_precision"] = {"id": 123, "name": precision}
    tables, quality = normalize(inputs([record]))
    row = tables["launch_record_current"].row(0, named=True)
    assert row["net_precision_name"] == precision
    assert row["is_orbital_attempt"] is False
    assert quality["sample_metrics"]["success_share"] is None


def test_correction_dedup_and_reclassification(records: list[dict[str, Any]]) -> None:
    old = synthetic(records[2], 70, 3)
    correction = deepcopy(old)
    correction["status"] = {"id": 7, "name": "Partial Failure"}
    correction["last_updated"] = "2026-11-01T12:00:00Z"
    tables, quality = normalize(inputs([correction, old, correction, old]))
    assert tables["launch_record_current"].height == 1
    assert tables["launch_observation"].height == 2
    assert quality["sample_metrics"]["success"] == 0
    assert quality["sample_metrics"]["partial_failure"] == 1


def test_unknown_and_contradictory_status_fail_quality(records: list[dict[str, Any]]) -> None:
    unknown = synthetic(records[2], 80, 999)
    future_success = synthetic(records[-1], 81, 3)
    tables, quality = normalize(inputs([unknown, future_success]))
    assert quality["passed"] is False
    assert len(quality["issues"]) == 2
    assert tables["fact_launch_attempt"].is_empty()


def test_family_bridge_never_multiplies_attempt_fact(records: list[dict[str, Any]]) -> None:
    tables, _ = normalize(inputs([records[2]]))
    assert tables["bridge_configuration_family"].height == 2
    assert tables["fact_launch_attempt"].height == 1
    assert (
        tables["dim_vehicle_family"].filter(pl.col("family_id") == 176)["parent_family_id"].item()
        == 1
    )


def test_rideshare_payloads_do_not_multiply_launches(records: list[dict[str, Any]]) -> None:
    record = synthetic(records[2], 90, 3)
    record["rocket"]["payloads"] = [{"id": 1}, {"id": 2}, {"id": 3}]
    tables, _ = normalize(inputs([record]))
    assert tables["launch_record_current"]["structured_payload_count"].item() == 3
    assert tables["fact_launch_attempt"].height == 1


def test_source_counter_and_media_updates_do_not_create_observations(
    records: list[dict[str, Any]],
) -> None:
    first = deepcopy(records[2])
    later = deepcopy(first)
    later["last_updated"] = "2026-11-01T12:00:00Z"
    later["image"] = {"url": "https://example.invalid/different-image.png"}
    later["launch_service_provider"]["total_launch_count"] = 99999
    tables, _ = normalize(inputs([first, later]))
    assert tables["launch_observation"].height == 1
    assert tables["launch_record_current"].height == 1


def test_all_live_statuses_have_a_reviewed_mapping(records: list[dict[str, Any]]) -> None:
    path = Path(__file__).parent / "fixtures/ll2_statuses.json"
    statuses = json.loads(path.read_text())["results"]
    cases = [synthetic(records[2], 200 + status["id"], status["id"]) for status in statuses]
    _, quality = normalize(inputs(cases))
    assert quality["passed"]


def test_invalid_coordinates_fail_validation(records: list[dict[str, Any]]) -> None:
    record = deepcopy(records[0])
    record["pad"]["latitude"] = 150
    _, quality = normalize(inputs([record]))
    assert not quality["passed"]
    assert "Invalid geographic coordinates in dim_pad" in quality["issues"]


def test_real_1957_failure_is_in_the_denominator() -> None:
    path = Path(__file__).parent / "fixtures/ll2_history_1957.json"
    records = json.loads(path.read_text(encoding="utf-8"))["results"]
    tables, quality = normalize(inputs(records))
    assert quality["passed"]
    assert tables["fact_launch_attempt"].height == 3
    assert quality["sample_metrics"]["success"] == 2
    assert quality["sample_metrics"]["failure"] == 1
