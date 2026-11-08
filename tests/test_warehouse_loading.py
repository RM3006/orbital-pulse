import json
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest
from launch_atlas.cli import main
from launch_atlas.config import Settings
from launch_atlas.pipeline import import_raw, rebuild_state
from launch_atlas.storage.exports import export_current
from launch_atlas.storage.snapshots import create_input_snapshot
from launch_atlas.storage.state import StateStore
from launch_atlas.warehouse.loading import (
    CloudTarget,
    TableLoad,
    TableReceipt,
    execute_load_plan,
    prepare_load_plan,
)


def make_load_plan(tmp_path: Path):
    raw_root = tmp_path / "source"
    data_dir = tmp_path / "accepted"
    fixture = Path(__file__).parent / "fixtures" / "ll2_public_sample.json"
    import_raw(
        Settings(data_dir=raw_root),
        fixture,
        "warehouse-fixture",
        "2026-11-01T16:52:44Z",
    )
    rebuild_state(
        Settings(data_dir=raw_root),
        data_dir,
        include_run_ids={"warehouse-fixture"},
    )
    state = StateStore(data_dir)
    state.create_plan(
        "warehouse-partial-history",
        "backfill",
        "2026-11-01T00:00:00Z",
        {"parser_version": "2", "page_size": 100},
        [
            {
                "window_id": "warehouse-history-1958-pass-1",
                "stream_id": "history-1958",
                "start_utc": "1958-01-01T00:00:00Z",
                "end_utc": "1959-01-01T00:00:00Z",
                "query": {"include_suborbital": False, "ordering": "net"},
                "initial_url": "https://ll.thespacedevs.com/2.3.0/launches/previous/",
            }
        ],
    )
    state.set_plan_status("warehouse-partial-history", "in_progress")
    state.set_latest_refresh_asof("2026-11-01T12:00:00Z")
    export_current(Settings(data_dir=data_dir))
    snapshot = create_input_snapshot(Settings(data_dir=data_dir))
    plan = prepare_load_plan(data_dir, snapshot["input_snapshot_id"], CloudTarget())
    return data_dir, snapshot["input_snapshot_id"], plan


class FakeBigQuery:
    def __init__(self) -> None:
        self.jobs: dict[str, TableReceipt] = {}
        self.fail_table: str | None = None
        self.load_attempts = 0

    def load_table(self, item: TableLoad, local_path: Path, location: str) -> TableReceipt:
        self.load_attempts += 1
        if self.fail_table == item.table_name:
            self.fail_table = None
            raise RuntimeError("simulated BigQuery job failure")
        assert local_path.is_file()
        assert local_path.suffix == ".parquet"
        assert location == "replace-with-colocated-location"
        previous = self.jobs.get(item.job_id)
        if previous is not None:
            return previous
        receipt = TableReceipt(item.table_name, item.destination, item.job_id, item.row_count)
        self.jobs[item.job_id] = receipt
        return receipt


def test_load_plan_tags_nonempty_snapshot_rows_with_explicit_schema(tmp_path: Path) -> None:
    data_dir, snapshot_id, plan = make_load_plan(tmp_path)

    assert plan.snapshot_id == snapshot_id
    assert plan.release_eligible is False
    assert len(plan.tables) == 11
    attempts = next(table for table in plan.tables if table.table_name == "fact_launch_attempt")
    assert attempts.row_count > 0
    assert attempts.destination == (
        "placeholder-project.space_launch_atlas_source."
        f"{plan.source_table_prefix}_fact_launch_attempt"
    )
    assert attempts.job_id.startswith("sla_load_")
    assert {field["name"] for field in attempts.schema} >= {
        "launch_id",
        "outcome",
        "input_snapshot_id",
    }
    provider = pl.read_parquet(
        data_dir / "warehouse" / "prepared" / snapshot_id / "tables" / "dim_provider.parquet"
    )
    assert provider.height > 0
    assert provider["input_snapshot_id"].unique().to_list() == [snapshot_id]
    provider_load = next(table for table in plan.tables if table.table_name == "dim_provider")
    assert (
        next(field for field in provider_load.schema if field["name"] == "country_codes")["mode"]
        == "REPEATED"
    )
    document = json.loads(plan.plan_path.read_text(encoding="utf-8"))
    assert document["target"]["project_id"] == "placeholder-project"
    assert len(document["local_artifacts"]) == len(plan.local_artifacts)
    assert plan.summary()["cloud_calls_made"] is False


def test_warehouse_prepare_cli_is_offline_and_uses_placeholders(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir, snapshot_id, _ = make_load_plan(tmp_path)

    result = main(["warehouse-prepare", "--snapshot-id", snapshot_id, "--data-dir", str(data_dir)])

    summary = json.loads(capsys.readouterr().out)
    assert result == 0
    assert summary["cloud_calls_made"] is False
    assert summary["release_eligible"] is False
    assert summary["parquet_tables"] == 11
    assert any(row_count > 0 for row_count in summary["rows"].values())
    saved = json.loads((data_dir / summary["plan_path"]).read_text(encoding="utf-8"))
    assert saved["target"]["project_id"] == "placeholder-project"
    assert saved["target"]["location"] == "replace-with-colocated-location"


def test_dagster_local_prepare_job_graph_is_wired_without_cloud() -> None:
    pytest.importorskip("dagster")
    from launch_atlas.warehouse.orchestration import local_warehouse_prepare_job

    graph = local_warehouse_prepare_job.graph
    dependencies = {invocation.name: inputs for invocation, inputs in graph.dependencies.items()}

    assert graph.node_names() == ["pin_warehouse_inputs", "prepare_warehouse_plan"]
    dependency = cast(Any, dependencies["prepare_warehouse_plan"]["pinned_inputs"])
    assert dependency.node == "pin_warehouse_inputs"


def test_local_integrity_and_load_retry_are_idempotent(tmp_path: Path) -> None:
    _, _, plan = make_load_plan(tmp_path)
    bigquery = FakeBigQuery()

    first = execute_load_plan(plan, bigquery)
    second = execute_load_plan(plan, bigquery)

    assert first["status"] == second["status"] == "ready"
    assert first["plan_id"] == second["plan_id"] == plan.plan_id
    assert len(first["local_artifacts_verified"]) == len(plan.local_artifacts)
    assert len(bigquery.jobs) == len(plan.tables)
    assert len(first["table_receipts"]) == len(plan.tables)
    assert first["release_eligible"] is False


def test_partial_load_retry_never_marks_new_plan_ready(tmp_path: Path) -> None:
    _, _, plan = make_load_plan(tmp_path)
    bigquery = FakeBigQuery()
    receipt_path = plan.plan_path.with_name("load-receipt.json")

    bigquery.fail_table = "stage_flight"
    with pytest.raises(RuntimeError, match="BigQuery job failure"):
        execute_load_plan(plan, bigquery)
    assert not receipt_path.exists()
    assert len(bigquery.jobs) > 0
    previously_loaded_jobs = set(bigquery.jobs)

    result = execute_load_plan(plan, bigquery)

    assert result["status"] == "ready"
    assert len(bigquery.jobs) == len(plan.tables)
    assert previously_loaded_jobs <= set(bigquery.jobs)
    assert receipt_path.is_file()


def test_create_only_collision_and_local_tampering_fail_before_load(tmp_path: Path) -> None:
    _, _, plan = make_load_plan(tmp_path)
    bigquery = FakeBigQuery()
    first = plan.local_artifacts[0]
    first.local_path.write_bytes(first.local_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="Local artifact changed after planning"):
        execute_load_plan(plan, bigquery)
    assert not bigquery.jobs


def test_pinned_parquet_tamper_is_rejected_during_preparation(tmp_path: Path) -> None:
    data_dir, snapshot_id, _ = make_load_plan(tmp_path)
    snapshot = json.loads(
        (data_dir / "snapshots" / f"{snapshot_id}.json").read_text(encoding="utf-8")
    )
    table_path = (
        data_dir
        / "exports"
        / "generations"
        / snapshot["export"]["generation_id"]
        / "launch_record_current.parquet"
    )
    table_path.write_bytes(table_path.read_bytes() + b"tamper")

    with pytest.raises(ValueError, match="Pinned Parquet checksum mismatch"):
        prepare_load_plan(data_dir, snapshot_id)


def test_bigquery_adapter_loads_local_file_and_reuses_conflicted_job(tmp_path: Path) -> None:
    pytest.importorskip("google.cloud.bigquery")
    from google.api_core.exceptions import Conflict
    from launch_atlas.warehouse.loading import GoogleBigQuerySourceLoader

    item = TableLoad(
        table_name="fact_launch_attempt",
        destination="placeholder-project.space_launch_atlas_source.fact_launch_attempt",
        artifact_name="snapshots/example/tables/fact_launch_attempt.parquet",
        sha256="a" * 64,
        job_id="sla_load_" + "b" * 64,
        row_count=17,
        schema=(
            {"name": "launch_id", "type": "STRING", "mode": "REQUIRED"},
            {"name": "input_snapshot_id", "type": "STRING", "mode": "REQUIRED"},
        ),
    )
    local_path = tmp_path / "fact_launch_attempt.parquet"
    local_path.write_bytes(b"local parquet fixture")

    class FakeJob:
        output_rows = 17
        error_result = None

        def result(self, timeout: int) -> None:
            assert timeout == 1800

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0
            self.get_job_calls = 0
            self.config: Any = None
            self.job = FakeJob()

        def load_table_from_file(
            self, parquet_file: Any, destination: str, **kwargs: object
        ) -> FakeJob:
            self.calls += 1
            assert parquet_file.read() == b"local parquet fixture"
            assert destination == item.destination
            assert kwargs["rewind"] is True
            assert kwargs["size"] == len(b"local parquet fixture")
            assert kwargs["job_id"] == item.job_id
            assert kwargs["location"] == "replace-with-colocated-location"
            self.config = kwargs["job_config"]
            raise Conflict("deterministic job already exists")

        def get_job(self, job_id: str, **kwargs: object) -> FakeJob:
            self.get_job_calls += 1
            assert job_id == item.job_id
            assert kwargs["project"] == "placeholder-project"
            return self.job

    client = FakeClient()
    adapter = GoogleBigQuerySourceLoader(client, "placeholder-project")
    receipt = adapter.load_table(
        item,
        local_path,
        "replace-with-colocated-location",
    )

    assert receipt.job_id == item.job_id
    assert receipt.row_count == 17
    assert client.calls == client.get_job_calls == 1
    assert client.config.autodetect is False
    assert client.config.source_format == "PARQUET"
    assert client.config.write_disposition == "WRITE_TRUNCATE"
    assert [field.name for field in client.config.schema] == [
        "launch_id",
        "input_snapshot_id",
    ]
