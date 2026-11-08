import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from launch_atlas.warehouse.releases import (
    CandidateEvidence,
    ReleaseManifest,
    build_release_manifest,
)


def synthetic_release(name: str) -> ReleaseManifest:
    snapshot_id = "sla-input-v1-" + "a" * 64
    evidence = CandidateEvidence(
        input_snapshot_id=snapshot_id,
        load_receipt={
            "status": "ready",
            "snapshot_id": snapshot_id,
            "release_eligible": True,
            "plan_id": f"plan-{name}",
            "plan_sha256": "f" * 64,
        },
        dbt_invocation_id=f"synthetic-{name}",
        dbt_manifest_sha256="b" * 64,
        dbt_run_results_sha256="c" * 64,
        dbt_run_status="success",
        dbt_test_count=1,
        dbt_test_failures=0,
        candidate_validation_passed=True,
        candidate_validation_errors=(),
        reporting_row_counts={
            "mart_launch_attempt_daily": 1,
            "mart_family_outcomes_daily": 1,
            "mart_stage_flight": 1,
            "mart_upcoming_schedule": 1,
        },
        analysis_as_of_utc="2026-11-08T00:00:00Z",
    )
    return build_release_manifest(evidence)


def test_bigquery_release_backend_uses_cas_transaction_and_bounded_query_config() -> None:
    pytest.importorskip("google.cloud.bigquery")
    from launch_atlas.warehouse.bigquery_releases import BigQueryReleaseBackend

    class FakeJob:
        job_id = "fake-job-id"
        state = "DONE"
        total_bytes_processed = 10
        total_bytes_billed = 10

        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def result(self, timeout: int) -> list[object]:
            assert timeout == 1800
            return self._rows

    class FakeClient:
        def __init__(self) -> None:
            self.sql = ""
            self.config: Any = None
            self.location = ""

        def query(self, sql: str, job_config: object, location: str) -> FakeJob:
            self.sql = sql
            self.config = job_config
            self.location = location
            return FakeJob([type("Row", (), {"committed": True})()])

    client = FakeClient()
    backend = BigQueryReleaseBackend(
        client,
        "atlas-test-project",
        "space_launch_atlas_scratch",
        "US",
        metadata_table="release_manifest_test",
        pointer_table="active_release_test",
    )

    assert backend.publish_validated(synthetic_release("a"), None) is True
    assert "BEGIN TRANSACTION" in client.sql
    assert "MERGE `atlas-test-project.space_launch_atlas_scratch.active_release_test`" in client.sql
    assert client.location == "US"
    assert client.config.maximum_bytes_billed == 1_000_000_000
    assert {param.name for param in client.config.query_parameters} == {
        "release_id",
        "manifest_sha256",
        "manifest_json",
        "expected_previous_release_id",
    }


@pytest.mark.integration
def test_bigquery_scratch_release_publish_cas_and_rollback(tmp_path: Path) -> None:
    project_id = os.environ.get("GCP_PROJECT_ID")
    location = os.environ.get("GCP_LOCATION")
    if not project_id or not location or os.environ.get("RUN_BIGQUERY_INTEGRATION") != "1":
        pytest.skip("Set RUN_BIGQUERY_INTEGRATION=1 with project/location and local ADC")

    from google.cloud import bigquery
    from launch_atlas.warehouse.bigquery_releases import BigQueryReleaseBackend

    suffix = uuid4().hex[:16]
    client = bigquery.Client(project=project_id)
    backend = BigQueryReleaseBackend(
        client,
        project_id,
        "space_launch_atlas_scratch",
        location,
        metadata_table=f"sla_test_release_{suffix}",
        pointer_table=f"sla_test_active_{suffix}",
    )
    backend.ensure_tables()
    release_a = synthetic_release(f"a-{suffix}")
    release_b = synthetic_release(f"b-{suffix}")
    release_c = synthetic_release(f"c-{suffix}")

    assert backend.active_release_id() is None
    assert backend.publish_validated(release_a, None)
    assert backend.active_release_id() == release_a.release_id
    assert backend.publish_validated(release_b, release_a.release_id)
    assert backend.active_release_id() == release_b.release_id
    assert not backend.publish_validated(release_c, release_a.release_id)
    assert backend.active_release_id() == release_b.release_id
    assert backend.retained_release(release_a.release_id) == release_a
    assert backend.activate_retained(release_a.release_id, release_b.release_id)
    assert backend.active_release_id() == release_a.release_id
    assert backend.retained_release(release_b.release_id) == release_b

    for table_id in (backend.metadata_table, backend.pointer_table):
        table = client.get_table(f"{project_id}.space_launch_atlas_scratch.{table_id}")
        assert table.expires is not None
    assert backend.job_evidence
    assert all(
        item["bytes_billed"] is None or item["bytes_billed"] <= 1_000_000_000
        for item in backend.job_evidence
    )
    (tmp_path / "bigquery-release-job-evidence.json").write_text(
        json.dumps(backend.job_evidence, indent=2), encoding="utf-8"
    )
