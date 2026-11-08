import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl
import pytest
from launch_atlas.config import Settings
from launch_atlas.pipeline import import_raw, rebuild_state
from launch_atlas.storage.exports import export_current
from launch_atlas.storage.state import StateStore


def _apply(store: StateStore, acquisition_id: str, record: dict[str, Any]) -> None:
    store.apply_acquisition(
        acquisition_id,
        acquisition_id,
        [(record, {"artifact": "fixture.json", "observed_at_utc": "2026-11-01T17:00:00Z"})],
    )


def test_generation_is_consistent_and_failed_candidate_keeps_active(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings = Settings(data_dir=tmp_path)
    state = StateStore(tmp_path)
    _apply(state, "valid", deepcopy(records[2]))
    accepted = export_current(settings)
    active_path = tmp_path / "exports" / "active.json"
    assert json.loads(active_path.read_text()) == accepted
    generation = tmp_path / "exports" / "generations" / accepted["generation_id"]
    assert (generation / "fact_launch_attempt.parquet").exists()
    assert accepted["files"]["launch_record_current.parquet"]["rows"] == 1

    invalid = deepcopy(records[3])
    invalid["status"] = {"id": 999, "name": "Unknown"}
    invalid["last_updated"] = "2026-11-02T12:00:00Z"
    _apply(state, "invalid", invalid)
    with pytest.raises(ValueError, match="quality checks"):
        export_current(settings)
    assert json.loads(active_path.read_text()) == accepted


def test_persistent_observation_export_has_stable_event_identity(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings = Settings(data_dir=tmp_path)
    state = StateStore(tmp_path)
    original = deepcopy(records[2])
    changed = deepcopy(original)
    changed["status"] = {"id": 7, "name": "Partial Failure"}
    changed["last_updated"] = "2026-11-02T12:00:00Z"
    returned = deepcopy(original)
    returned["last_updated"] = "2026-11-03T12:00:00Z"
    for acquisition_id, record in (("a1", original), ("a2", changed), ("a3", returned)):
        _apply(state, acquisition_id, record)

    first = export_current(settings)
    first_path = tmp_path / "exports" / "generations" / first["generation_id"]
    first_events = pl.read_parquet(first_path / "launch_observation.parquet")
    second = export_current(settings)
    second_path = tmp_path / "exports" / "generations" / second["generation_id"]
    second_events = pl.read_parquet(second_path / "launch_observation.parquet")

    assert first_events.height == 3
    assert first_events["observation_event_key"].n_unique() == 3
    assert (
        first_events["observation_event_key"].to_list()
        == second_events["observation_event_key"].to_list()
    )
    assert set(first_events["acquisition_id"].to_list()) == {"a1", "a2", "a3"}
    assert first["table_contracts"]["launch_observation"]["key"] == ["observation_event_key"]


def test_shared_request_budget_covers_multiple_clients(tmp_path: Path) -> None:
    import httpx
    from launch_atlas.clients.ll2 import LL2Client, Page, PauseRun, RequestBudget
    from launch_atlas.config import Settings
    from launch_atlas.storage.local import LocalStore

    budget = RequestBudget(2)
    settings = Settings(data_dir=tmp_path, max_requests=2)
    store = LocalStore(tmp_path)
    url = settings.base_url + "launches/previous/?limit=1&mode=detailed&include_suborbital=false"
    response_body = b'{"count":0,"next":null,"results":[]}'
    for _ in range(2):
        client = LL2Client(
            settings,
            store,
            budget=budget,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"Content-Type": "application/json"}, content=response_body
                )
            ),
        )
        assert client.fetch(url, lambda *_: None) == Page(count=0, next=None, results=[])
        client.close()
    assert budget.used == 2
    third = LL2Client(settings, store, budget=budget)
    with pytest.raises(PauseRun, match="budget_stopped"):
        third.fetch(url, lambda *_: None)
    third.close()


def test_offline_rebuild_copies_raw_and_recreates_state_without_quota(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "rebuilt"
    fixture = Path(__file__).parent / "fixtures" / "ll2_public_sample.json"
    settings = Settings(data_dir=source)
    import_raw(settings, fixture, "fixture-import", "2026-11-01T16:52:44Z")
    original_quota = (source / "quota.json").exists()

    result = rebuild_state(settings, output, include_run_ids={"fixture-import"})
    assert result["runs_applied"] == 1
    assert result["state"]["launch_current"] == 6
    assert result["generation"]["quality_passed"]
    assert (output / "raw" / "ll2" / "fixture-import" / "manifest.json").exists()
    assert (source / "quota.json").exists() is original_quota


def test_rebuild_preserves_completed_refresh_horizon(tmp_path: Path) -> None:
    source = tmp_path / "source-with-refresh"
    output = tmp_path / "rebuilt-with-refresh"
    fixture = Path(__file__).parent / "fixtures" / "ll2_public_sample.json"
    settings = Settings(data_dir=source)
    import_raw(settings, fixture, "fixture-refresh", "2026-11-01T16:52:44Z")
    manifest_path = source / "raw" / "ll2" / "fixture-refresh" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "plan_type": "refresh",
            "plan_status": "complete",
            "plan_frozen_as_of_utc": "2026-11-07T09:08:20.459707+00:00",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    rebuilt = rebuild_state(settings, output, include_run_ids={"fixture-refresh"})

    assert rebuilt["generation"]["coverage"]["upcoming_as_of_utc"] == (
        "2026-11-07T09:08:20.459707+00:00"
    )


def test_export_upcoming_uses_calendar_precision_overlap(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings = Settings(data_dir=tmp_path)
    state = StateStore(tmp_path)
    state.create_plan(
        "refresh-asof",
        "refresh",
        "2026-11-07T00:00:00Z",
        {"upcoming_horizon_days": 90},
        [],
    )
    state.set_plan_status("refresh-asof", "complete")
    yearly = deepcopy(records[-1])
    yearly["id"] = str(UUID(int=9001))
    yearly["net"] = "2027-12-31T23:59:00Z"
    yearly["net_precision"] = {"id": 14, "name": "Year"}
    yearly["status"] = {"id": 2, "name": "To Be Confirmed"}
    exact = deepcopy(yearly)
    exact["id"] = str(UUID(int=9002))
    exact["net_precision"] = {"id": 5, "name": "Day"}
    unknown = deepcopy(yearly)
    unknown["id"] = str(UUID(int=9003))
    unknown["net_precision"] = {"id": 999, "name": "Future Precision"}
    state.apply_acquisition(
        "upcoming-candidates",
        "upcoming-candidates",
        [
            (
                record,
                {"artifact": "raw/fixture.json.gz", "observed_at_utc": "2026-11-07T00:01:00Z"},
            )
            for record in (yearly, exact, unknown)
        ],
    )

    generation = export_current(settings)
    output = tmp_path / "exports" / "generations" / generation["generation_id"]
    table = pl.read_parquet(output / "fact_upcoming_schedule.parquet")
    assert table["launch_id"].to_list() == [yearly["id"]]
    assert table["interval_basis"].item() == "year"
    assert table["horizon_start_utc"].item().year == 2026
    assert generation["coverage"]["upcoming_unsupported_precision"] == 1
