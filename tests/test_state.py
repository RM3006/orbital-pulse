from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from launch_atlas.storage.state import StateStore


def acquisition(
    store: StateStore,
    acquisition_id: str,
    records: list[dict[str, Any]],
    observed_at: str,
) -> dict[str, Any]:
    payload = [
        (record, {"artifact": f"fixture-{acquisition_id}.json", "observed_at_utc": observed_at})
        for record in records
    ]
    return store.apply_acquisition(acquisition_id, acquisition_id, payload)


def test_cross_run_transitions_keep_a_b_a_and_replay_is_idempotent(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    store = StateStore(tmp_path)
    first = deepcopy(records[2])
    changed = deepcopy(first)
    changed["status"] = {"id": 7, "name": "Partial Failure"}
    changed["last_updated"] = "2026-11-02T12:00:00Z"
    returned = deepcopy(first)
    returned["last_updated"] = "2026-11-03T12:00:00Z"

    assert acquisition(store, "a1", [first], "2026-11-01T17:00:00Z")["new"] == 1
    assert acquisition(store, "a1", [first], "2026-11-01T17:00:00Z")["already_applied"]
    assert acquisition(store, "a2", [changed], "2026-11-02T17:00:00Z")["changed"] == 1
    assert acquisition(store, "a3", [returned], "2026-11-03T17:00:00Z")["changed"] == 1
    assert store.summary() == {
        "acquisition": 3,
        "launch_current": 1,
        "record_sighting": 3,
        "observation_event": 3,
        "state_conflict": 0,
        "raw_request": 0,
    }


def test_observation_event_keys_survive_state_rebuild(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    first_state = StateStore(tmp_path / "event-keys-original")
    rebuilt_state = StateStore(tmp_path / "event-keys-rebuilt")
    original = deepcopy(records[2])
    changed = deepcopy(original)
    changed["status"] = {"id": 7, "name": "Partial Failure"}
    changed["last_updated"] = "2026-11-02T12:00:00Z"
    returned = deepcopy(original)
    returned["last_updated"] = "2026-11-03T12:00:00Z"
    acquisitions = (("a1", original), ("a2", changed), ("a3", returned))

    for store in (first_state, rebuilt_state):
        for acquisition_id, record in acquisitions:
            acquisition(store, acquisition_id, [record], "2026-11-01T17:00:00Z")

    first = first_state.observation_records()
    rebuilt = rebuilt_state.observation_records()
    keys = [event["observation_event_key"] for event in first]
    assert len(keys) == 3
    assert len(set(keys)) == 3
    assert keys == [event["observation_event_key"] for event in rebuilt]
    assert all(event["record_ordinal"] == 0 for event in first)
    assert {event["semantic_hash_version"] for event in first} == {"2"}


def test_stale_and_equal_timestamp_conflict_do_not_replace_current(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    store = StateStore(tmp_path)
    newest = deepcopy(records[2])
    newest["last_updated"] = "2026-11-03T12:00:00Z"
    acquisition(store, "newest", [newest], "2026-11-03T17:00:00Z")
    stale = deepcopy(newest)
    stale["status"] = {"id": 4, "name": "Failure"}
    stale["last_updated"] = "2026-11-02T12:00:00Z"
    assert acquisition(store, "stale", [stale], "2026-11-04T17:00:00Z")["stale"] == 1
    tied = deepcopy(newest)
    tied["status"] = {"id": 7, "name": "Partial Failure"}
    assert acquisition(store, "tied", [tied], "2026-11-05T17:00:00Z")["conflict"] == 1

    with store.session() as connection:
        row = connection.execute("SELECT raw_record_json FROM launch_current").fetchone()
        assert '"id":3' in row[0] and '"name":"Launch Successful"' in row[0]
        assert connection.execute("SELECT COUNT(*) FROM observation_event").fetchone()[0] == 1


def test_failed_acquisition_rolls_back_all_changes(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    store = StateStore(tmp_path)
    valid = deepcopy(records[2])
    invalid = deepcopy(records[3])
    invalid.pop("last_updated")
    with pytest.raises(KeyError):
        acquisition(store, "bad", [valid, invalid], "2026-11-01T17:00:00Z")
    assert store.summary() == {
        "acquisition": 0,
        "launch_current": 0,
        "record_sighting": 0,
        "observation_event": 0,
        "state_conflict": 0,
        "raw_request": 0,
    }


def test_request_evidence_keeps_send_and_receipt_times(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    store = StateStore(tmp_path)
    record = deepcopy(records[2])
    evidence = [
        {
            "url": "https://ll.thespacedevs.com/2.3.0/launches/previous/?limit=1",
            "request_started_at_utc": "2026-11-01T16:59:59Z",
            "observed_at_utc": "2026-11-01T17:00:01Z",
            "http_status": 200,
            "artifact": "raw/fixture.json.gz",
            "sha256": "abc123",
        }
    ]
    store.apply_acquisition(
        "request-run",
        "request-run",
        [(record, {"artifact": "raw/fixture.json.gz", "observed_at_utc": "2026-11-01T17:00:01Z"})],
        request_evidence=evidence,
    )
    with store.session() as connection:
        request = connection.execute("SELECT * FROM raw_request").fetchone()
    assert request["requested_at_utc"] == "2026-11-01T16:59:59+00:00"
    assert request["response_received_at_utc"] == "2026-11-01T17:00:01+00:00"


def test_frozen_window_only_advances_cursor_in_state_commit(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    store = StateStore(tmp_path)
    plan = {
        "parser_version": "2",
        "page_size": 100,
        "start_utc": "2026-01-01T00:00:00Z",
        "end_utc": "2027-01-01T00:00:00Z",
    }
    windows = [
        {
            "window_id": "history-2026-pass-1",
            "stream_id": "history",
            "start_utc": plan["start_utc"],
            "end_utc": plan["end_utc"],
            "query": {"ordering": "net", "include_suborbital": False},
            "initial_url": "https://ll.thespacedevs.com/2.3.0/launches/previous/?offset=0",
        }
    ]
    store.create_plan("history-2026", "backfill", "2026-11-07T00:00:00Z", plan, windows)
    with pytest.raises(ValueError, match="different frozen parameters"):
        store.create_plan(
            "history-2026",
            "backfill",
            "2026-11-07T00:00:00Z",
            {**plan, "page_size": 50},
            [],
        )
    assert store.coverage_cursors() == {}
    store.checkpoint_window("history-2026-pass-1", None)
    store.mark_window_extracted("history-2026-pass-1")
    store.validate_window("history-2026-pass-1", True, 1, 1)
    record = deepcopy(records[2])
    result = store.apply_acquisition(
        "history-2026-page-set",
        "history-2026-page-set",
        [(record, {"artifact": "fixture.json", "observed_at_utc": "2026-11-07T00:01:00Z"})],
        window_id="history-2026-pass-1",
    )
    assert result["new"] == 1
    assert store.window("history-2026-pass-1")["extraction_status"] == "committed"
    assert store.coverage_cursors()["history"] == "2027-01-01T00:00:00+00:00"


def test_valid_empty_window_advances_cursor_but_incomplete_window_does_not(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    windows = [
        {
            "window_id": f"recent-{suffix}",
            "stream_id": "recent",
            "start_utc": f"2026-0{month}-01T00:00:00Z",
            "end_utc": f"2026-0{month + 1}-01T00:00:00Z",
            "query": {"month": month},
            "initial_url": f"https://ll.thespacedevs.com/2.3.0/launches/previous/?offset=0&month={month}",
        }
        for month, suffix in ((1, "jan"), (2, "feb"))
    ]
    store.create_plan("refresh", "refresh", "2026-03-01T00:00:00Z", {}, windows)
    store.checkpoint_window("recent-feb", None)
    store.mark_window_extracted("recent-feb")
    store.validate_window("recent-feb", False, 0, 0)
    with pytest.raises(ValueError, match="validated empty"):
        store.commit_empty_window("recent-feb")
    assert store.coverage_cursors() == {}

    store.checkpoint_window("recent-jan", None)
    store.mark_window_extracted("recent-jan")
    store.validate_window("recent-jan", True, 0, 0)
    store.commit_empty_window("recent-jan")
    assert store.coverage_cursors()["recent"] == "2026-02-01T00:00:00+00:00"
