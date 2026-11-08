import copy
import json
from pathlib import Path
from typing import Any

import pytest
from launch_atlas.cli import main
from launch_atlas.config import Settings
from launch_atlas.pipeline import import_raw, rebuild_state
from launch_atlas.storage.exports import export_current
from launch_atlas.storage.snapshots import create_input_snapshot
from launch_atlas.storage.state import StateStore


def test_a_b_a_event_keys_survive_raw_rebuild_and_parquet_export(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    source = tmp_path / "source"
    fixture_record = records[2]
    first = copy.deepcopy(fixture_record)
    changed = copy.deepcopy(first)
    changed["status"] = {"id": 7, "name": "Partial Failure"}
    changed["last_updated"] = "2026-11-02T12:00:00Z"
    returned = copy.deepcopy(first)
    returned["last_updated"] = "2026-11-03T12:00:00Z"
    variants = (("a1", first), ("a2", changed), ("a3", returned))
    run_ids = set()
    for run_id, record in variants:
        page = tmp_path / f"{run_id}.json"
        page.write_text(
            json.dumps({"count": 1, "next": None, "results": [record]}),
            encoding="utf-8",
        )
        import_raw(
            Settings(data_dir=source),
            page,
            run_id,
            f"2026-11-0{int(run_id[1])}T17:00:00Z",
        )
        run_ids.add(run_id)

    original = rebuild_state(
        Settings(data_dir=source), tmp_path / "rebuild-1", include_run_ids=run_ids
    )
    repeated = rebuild_state(
        Settings(data_dir=source), tmp_path / "rebuild-2", include_run_ids=run_ids
    )

    def event_keys(result: dict[str, Any]) -> list[str]:
        output = Path(result["output_dir"])
        active = json.loads((output / "exports" / "active.json").read_text(encoding="utf-8"))
        table_path = (
            output
            / "exports"
            / "generations"
            / active["generation_id"]
            / "launch_observation.parquet"
        )
        import polars as pl

        return pl.read_parquet(table_path)["observation_event_key"].to_list()

    first_keys = event_keys(original)
    second_keys = event_keys(repeated)
    assert original["state"]["observation_event"] == 3
    assert len(set(first_keys)) == 3
    assert first_keys == second_keys


def make_snapshot_ready_state(tmp_path: Path) -> Path:
    source = tmp_path / "raw-source"
    output = tmp_path / "accepted"
    fixture = Path(__file__).parent / "fixtures" / "ll2_public_sample.json"
    import_raw(
        Settings(data_dir=source),
        fixture,
        "fixture-import",
        "2026-11-01T16:52:44Z",
    )
    rebuild_state(Settings(data_dir=source), output, include_run_ids={"fixture-import"})
    return output


def test_snapshot_is_versioned_repeatable_and_retains_incomplete_plan(tmp_path: Path) -> None:
    data_dir = make_snapshot_ready_state(tmp_path)
    state = StateStore(data_dir)
    state.create_plan(
        "partial-history",
        "backfill",
        "2026-11-01T00:00:00Z",
        {"parser_version": "2", "page_size": 100},
        [
            {
                "window_id": "partial-history-1958-pass-1",
                "stream_id": "history-1958",
                "start_utc": "1958-01-01T00:00:00Z",
                "end_utc": "1959-01-01T00:00:00Z",
                "query": {"include_suborbital": False, "ordering": "net"},
                "initial_url": "https://ll.thespacedevs.com/2.3.0/launches/previous/",
            }
        ],
    )
    state.set_plan_status("partial-history", "in_progress")
    state.set_latest_refresh_asof("2026-11-01T12:00:00Z")

    export_current(Settings(data_dir=data_dir))
    first = create_input_snapshot(Settings(data_dir=data_dir))
    export_current(Settings(data_dir=data_dir))
    second = create_input_snapshot(Settings(data_dir=data_dir))
    path = data_dir / first["snapshot_path"]
    document = json.loads(path.read_text(encoding="utf-8"))

    assert first["input_snapshot_id"].startswith("sla-input-v1-")
    assert first["input_snapshot_id"] == second["input_snapshot_id"]
    assert document["content_sha256"] == first["content_sha256"]
    assert document["state"]["revision"] == 1
    assert document["export"]["quality_passed"] is True
    assert document["export"]["rows"]["launch_record_current"] == 6
    assert document["source_inputs"][0]["pages"][0]["records"] == 6
    assert document["state"]["plans"][0]["status"] == "in_progress"
    assert document["transformations"]["observation_event_key_version"] == "1"
    assert path.is_file()


def test_snapshot_refuses_tampered_export(tmp_path: Path) -> None:
    data_dir = make_snapshot_ready_state(tmp_path)
    active = json.loads((data_dir / "exports" / "active.json").read_text(encoding="utf-8"))
    generation = data_dir / "exports" / "generations" / active["generation_id"]
    table = generation / "launch_record_current.parquet"
    table.write_bytes(table.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="checksum or row count mismatch"):
        create_input_snapshot(Settings(data_dir=data_dir))


def test_input_snapshot_cli_runs_offline_and_reports_short_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = make_snapshot_ready_state(tmp_path)

    assert main(["input-snapshot", "--data-dir", str(data_dir)]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["input_snapshot_id"].startswith("sla-input-v1-")
    assert report["source_runs_verified"] == 1
    assert report["parquet_files_verified"] == 11
    assert report["quality_passed"] is True
