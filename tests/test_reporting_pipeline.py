from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from launch_atlas.config import Settings
from launch_atlas.pipeline import run_plan
from launch_atlas.storage.state import StateStore
from launch_atlas.warehouse.loading import (
    CloudTarget,
    GoogleBigQuerySourceLoader,
    LoadPlan,
    TableLoad,
    TableReceipt,
)
from launch_atlas.warehouse.releases import (
    REQUIRED_REPORTING_TABLES,
    CandidateRejected,
    ReleaseManifest,
    StalePublisher,
    build_release_manifest,
    publish_candidate,
    rollback_release,
)
from launch_atlas.warehouse.reporting import BigQueryReportingStore
from launch_atlas.warehouse.runner import (
    BuildResult,
    DbtBuilder,
    WarehousePipeline,
    validate_dbt_artifacts,
)
from launch_atlas.windows import create_backfill_plan


def complete_scope(data_dir: Path, changed: bool = False) -> Settings:
    """A real two-pass, mocked-source 1957 window; never override production eligibility."""
    page = json.loads((Path(__file__).parent / "fixtures/ll2_history_1957.json").read_text())
    if changed:
        page["results"][0]["status"] = {"id": 4, "name": "Launch Failure"}
        page["results"][0]["last_updated"] = "2026-11-08T00:00:00Z"
    settings = Settings(data_dir=data_dir, max_requests=2, page_size=100)
    state = StateStore(data_dir)
    create_backfill_plan(
        state,
        settings,
        "fixture-history",
        1957,
        1957,
        frozen_as_of=datetime(2026, 11, 8, tzinfo=UTC),
    )
    run_plan(
        settings,
        "fixture-history",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=page)),
    )
    assert state.plan("fixture-history")["status"] == "complete"
    assert state.summary()["launch_current"] == 3
    state.set_latest_refresh_asof("2026-11-08T00:00:00Z")
    return settings


class OfflineLoader:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def load_table(self, item: TableLoad, local_path: Path, location: str) -> TableReceipt:
        self.calls += 1
        if self.fail:
            raise RuntimeError("injected load failure")
        assert local_path.is_file()
        return TableReceipt(item.table_name, item.destination, item.job_id, item.row_count)


class OfflineBuilder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def build(self, plan: LoadPlan, run_dir: Path, candidate_prefix: str) -> BuildResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("injected dbt failure")
        return BuildResult(
            candidate_prefix,
            "a" * 64,
            "b" * 64,
            12,
            {name: name for name in REQUIRED_REPORTING_TABLES},
        )


class OfflineReporting:
    def __init__(self) -> None:
        self.active: str | None = None
        self.retained: dict[str, ReleaseManifest] = {}
        self.job_evidence: list[dict[str, Any]] = []
        self.retain_calls = 0
        self.fail_retention = False

    def inspect_candidate(self, relations: dict[str, str], snapshot_id: str):
        assert set(relations) == REQUIRED_REPORTING_TABLES
        return dict.fromkeys(relations, 1), dict.fromkeys(relations, "c" * 64)

    def retain_candidate(self, manifest: ReleaseManifest, relations: dict[str, str]) -> None:
        self.retain_calls += 1
        if self.fail_retention:
            raise RuntimeError("injected retention failure")
        self.retained[manifest.release_id] = manifest

    def active_release_id(self) -> str | None:
        return self.active

    def retained_release(self, release_id: str) -> ReleaseManifest | None:
        return self.retained.get(release_id)

    def publish_validated(self, manifest: ReleaseManifest, expected: str | None) -> bool:
        assert self.retained[manifest.release_id] == manifest
        if expected != self.active:
            return False
        self.active = manifest.release_id
        return True


def execute(pipeline: WarehousePipeline, raise_on_error: bool = True):
    from launch_atlas.warehouse.orchestration import execute_warehouse_pipeline

    result = execute_warehouse_pipeline(pipeline)
    if raise_on_error:
        assert result.success
    return result


@pytest.mark.local_asyncio
def test_full_dagster_chain_and_failures_preserve_last_good_data(tmp_path: Path) -> None:
    settings = complete_scope(tmp_path / "accepted")
    reporting = OfflineReporting()
    target = CloudTarget()
    pipeline = WarehousePipeline(
        settings, target, OfflineLoader(), OfflineBuilder(), cast(Any, reporting), publish=True
    )
    result = execute(pipeline)
    assert result.success
    first = reporting.active
    assert first is not None
    assert result.output_for_node("activate_reporting_release")["status"] == "published"
    retained = reporting.retained[first]
    for stage in ("load", "dbt", "retention", "stale"):
        reporting.fail_retention = stage == "retention"
        pipeline = WarehousePipeline(
            settings,
            target,
            OfflineLoader(stage == "load"),
            OfflineBuilder(stage == "dbt"),
            cast(Any, reporting),
            publish=True,
            expected_active=None if stage == "stale" else first,
        )
        failed = execute(pipeline, raise_on_error=False)
        assert not failed.success, stage
        assert reporting.active == first
        assert reporting.retained[first] == retained
        assert json.loads((pipeline.run_dir / "failure.json").read_text())["failed_steps"]


@pytest.mark.local_asyncio
def test_incomplete_history_builds_but_cannot_retain_or_activate(tmp_path: Path) -> None:
    settings = complete_scope(tmp_path / "accepted")
    StateStore(settings.data_dir).set_plan_status("fixture-history", "in_progress")
    reporting = OfflineReporting()
    pipeline = WarehousePipeline(
        settings,
        CloudTarget(),
        OfflineLoader(),
        OfflineBuilder(),
        cast(Any, reporting),
        publish=True,
    )
    failed = execute(pipeline, raise_on_error=False)
    assert not failed.success
    assert reporting.retain_calls == 0
    assert reporting.active is None
    built = execute(
        WarehousePipeline(
            settings, CloudTarget(), OfflineLoader(), OfflineBuilder(), cast(Any, reporting)
        )
    )
    assert built.success
    output = built.output_for_node("activate_reporting_release")
    assert output["status"] == "candidate_built"
    assert output["release_eligible"] is False


def test_dbt_evidence_rejects_skipped_tests_and_wrong_candidate_relations(tmp_path: Path) -> None:
    from launch_atlas.storage.exports import export_current
    from launch_atlas.storage.snapshots import create_input_snapshot
    from launch_atlas.warehouse.loading import prepare_load_plan

    settings = complete_scope(tmp_path / "accepted")
    export_current(settings)
    snapshot = create_input_snapshot(settings)
    plan = prepare_load_plan(settings.data_dir, snapshot["input_snapshot_id"], CloudTarget())
    prefix, dataset = "candidate_a_", "space_launch_atlas_scratch"
    nodes = {
        f"model.{name}": {
            "name": name,
            "resource_type": "model",
            "database": plan.target.project_id,
            "schema": dataset,
            "alias": prefix + name,
        }
        for name in REQUIRED_REPORTING_TABLES
    }
    nodes["test.meaningful"] = {"resource_type": "test"}
    manifest = {"metadata": {"invocation_id": "one"}, "nodes": nodes}
    results = {
        "metadata": {"invocation_id": "one"},
        "results": [
            {"unique_id": name, "status": "pass" if name.startswith("test.") else "success"}
            for name in nodes
        ],
    }
    target = tmp_path / "target"
    target.mkdir()

    def save() -> None:
        (target / "manifest.json").write_text(json.dumps(manifest))
        (target / "run_results.json").write_text(json.dumps(results))

    save()
    assert validate_dbt_artifacts(target, plan, prefix, dataset).test_count == 1
    results["results"][-1]["status"] = "skipped"
    save()
    with pytest.raises(CandidateRejected, match="skipped"):
        validate_dbt_artifacts(target, plan, prefix, dataset)
    results["results"][-1]["status"] = "pass"
    nodes["model.mart_landing"]["schema"] = "mutable_other_dataset"
    save()
    with pytest.raises(CandidateRejected, match="outside this candidate"):
        validate_dbt_artifacts(target, plan, prefix, dataset)


@pytest.mark.integration
def test_bigquery_real_rows_views_failures_and_rollback(tmp_path: Path) -> None:
    if os.getenv("RUN_BIGQUERY_INTEGRATION") != "1":
        pytest.skip("Explicit BigQuery integration opt-in required")
    from google.cloud import bigquery

    project, location = os.environ["GCP_PROJECT_ID"], os.environ["GCP_LOCATION"]
    scratch = "space_launch_atlas_scratch"
    client = bigquery.Client(project=project)
    store = BigQueryReportingStore(
        client,
        project,
        scratch,
        location,
        candidate_dataset=scratch,
        table_prefix="verify_" + uuid4().hex[:12] + "_",
        maximum_bytes_billed=100_000_000,
    )
    target = CloudTarget(project, "space_launch_atlas", location, source_dataset=scratch)
    loader = GoogleBigQuerySourceLoader(client, project)
    first = WarehousePipeline(
        complete_scope(tmp_path / "fixture-a"),
        target,
        loader,
        DbtBuilder(scratch),
        store,
        publish=True,
    )
    assert execute(first).success
    release_a = store.active_release_id()
    assert release_a
    manifest_a = store.retained_release(release_a)
    assert manifest_a
    # One statement reading 15 views touches 17 tables including metadata/pointer.
    # BigQuery's 10 MiB/table billing floor requires 170 MiB despite tiny fixture data.
    reader = BigQueryReportingStore(
        client,
        project,
        scratch,
        location,
        candidate_dataset=scratch,
        table_prefix=store.table_prefix,
        maximum_bytes_billed=250_000_000,
    )

    def readers() -> list[Any]:
        sql = " UNION ALL ".join(
            f"SELECT '{name}' AS name, COUNT(*) AS n, ANY_VALUE(release_id) AS release_id "
            f"FROM `{project}.{scratch}.{store.table_prefix}{name}`"
            for name in sorted(REQUIRED_REPORTING_TABLES)
        )
        return list(reader._query(sql))

    assert all(row.release_id == release_a for row in readers() if row.n)
    # Destroying/rebuilding a mutable candidate must not change retained readers.
    candidate = f"{project}.{scratch}.{first.candidate_prefix}mart_launch_attempt_daily"
    store._query(f"UPDATE `{candidate}` SET attempt_count = 999 WHERE TRUE")
    store.verify_retained(manifest_a)
    assert store.active_release_id() == release_a
    second = WarehousePipeline(
        complete_scope(tmp_path / "fixture-b", changed=True),
        target,
        loader,
        DbtBuilder(scratch),
        store,
        publish=True,
        expected_active=release_a,
    )
    assert execute(second).success
    release_b = store.active_release_id()
    assert release_b and release_b != release_a
    manifest_b = store.retained_release(release_b)
    assert manifest_b
    assert all(row.release_id == release_b for row in readers() if row.n)
    assert {row.name: row.n for row in readers()} == manifest_b.reporting_row_counts
    evidence_path = second.run_dir / "candidate-evidence.json"
    from launch_atlas.warehouse.releases import CandidateEvidence

    evidence = CandidateEvidence(**json.loads(evidence_path.read_text()))
    stale = replace(evidence, dbt_invocation_id="stale-" + uuid4().hex)
    stale_manifest = build_release_manifest(stale)
    relations = {
        name: f"{project}.{scratch}.{second.candidate_prefix}{name}"
        for name in REQUIRED_REPORTING_TABLES
    }
    store.retain_candidate(stale_manifest, relations)
    with pytest.raises(StalePublisher):
        publish_candidate(stale, store, release_a)
    assert store.active_release_id() == release_b
    assert rollback_release(release_a, store, release_b)["status"] == "rolled_back"
    assert all(row.release_id == release_a for row in readers() if row.n)
    rollback_release(release_b, store, release_a)
    # A damaged retained release cannot be reselected.
    store._query(
        f"UPDATE `{store._retained('mart_launch_attempt_daily')}` "
        "SET attempt_count = attempt_count + 1 WHERE release_id = @release_id",
        [("release_id", "STRING", release_a)],
    )
    with pytest.raises(CandidateRejected, match="changed or is missing"):
        rollback_release(release_a, store, release_b)
    assert store.active_release_id() == release_b
    broken = WarehousePipeline(
        complete_scope(tmp_path / "fixture-c"),
        target,
        loader,
        OfflineBuilder(fail=True),
        store,
        publish=True,
        expected_active=release_b,
    )
    assert not execute(broken, raise_on_error=False).success
    assert store.active_release_id() == release_b
    for name in REQUIRED_REPORTING_TABLES:
        assert client.get_table(store._retained(name)).expires is not None
    (tmp_path / "reporting-job-evidence.json").write_text(
        json.dumps(
            {
                "release_a": release_a,
                "release_b": release_b,
                "rows": manifest_b.reporting_row_counts,
                "jobs": store.job_evidence + reader.job_evidence,
                "query_bytes_billed": sum(
                    row["bytes_billed"] or 0 for row in store.job_evidence + reader.job_evidence
                ),
            },
            indent=2,
        )
    )
