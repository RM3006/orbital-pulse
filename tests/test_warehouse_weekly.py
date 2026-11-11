from __future__ import annotations

import sys
from contextlib import nullcontext
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "warehouse_weekly.py"
_SPEC = spec_from_file_location("orbital_pulse_warehouse_weekly", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
warehouse_weekly = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = warehouse_weekly
_SPEC.loader.exec_module(warehouse_weekly)


def snapshot_document(
    snapshot_id: str,
    *,
    file_hash: str = "data-hash",
    repository_commit: str = "commit-a",
    generation_id: str = "generation-a",
) -> dict[str, Any]:
    return {
        "input_snapshot_id": snapshot_id,
        "content_sha256": f"content-{snapshot_id}",
        "created_at_utc": "2026-11-11T12:00:00Z",
        "contract_version": 1,
        "source": "ll2",
        "state": {"revision": 217, "row_counts": {"launch_current": 1}},
        "export": {
            "state_revision": 217,
            "generation_id": generation_id,
            "manifest_sha256": f"manifest-{generation_id}",
            "files": {"launch.parquet": {"sha256": file_hash, "rows": 1}},
            "rows": {"launch_current": 1},
            "coverage": {"upcoming_as_of_utc": "2026-11-11T12:00:00Z"},
            "table_contracts": {},
            "quality_passed": True,
        },
        "transformations": {
            "code_revision": {
                "repository_commit": repository_commit,
                "source_files_sha256": "transform-hash",
                "project_version": "0.1.0",
                "source_file_count": 74,
            },
            "parser_version": "parser-v1",
        },
        "source_inputs": [{"manifest_sha256": "source-manifest-hash"}],
    }


def test_warehouse_fingerprint_ignores_generated_metadata_and_commit() -> None:
    original = snapshot_document("snapshot-a")
    regenerated = deepcopy(original)
    regenerated["input_snapshot_id"] = "snapshot-b"
    regenerated["content_sha256"] = "content-b"
    regenerated["created_at_utc"] = "2026-11-11T13:00:00Z"
    regenerated["export"]["generation_id"] = "generation-b"
    regenerated["export"]["manifest_sha256"] = "manifest-b"
    regenerated["transformations"]["code_revision"]["repository_commit"] = "commit-b"

    assert warehouse_weekly.warehouse_input_fingerprint(original) == (
        warehouse_weekly.warehouse_input_fingerprint(regenerated)
    )

    regenerated["export"]["files"]["launch.parquet"]["sha256"] = "changed-data-hash"
    assert warehouse_weekly.warehouse_input_fingerprint(original) != (
        warehouse_weekly.warehouse_input_fingerprint(regenerated)
    )


def test_warehouse_schedule_fails_closed_without_account_scope(monkeypatch) -> None:
    bigquery_calls: list[object] = []
    monkeypatch.setattr(warehouse_weekly, "configure_logging", lambda: None)
    monkeypatch.setattr(warehouse_weekly, "worker_lock", lambda _path: nullcontext())
    monkeypatch.setattr(
        warehouse_weekly,
        "ready_worker_state",
        lambda: {
            "input_snapshot_id": "new-snapshot",
            "state_counts": {"acquisition": 217},
        },
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "load_project_environment",
        lambda: {
            "GCP_PROJECT_ID": "test-project",
            "GCP_LOCATION": "europe-west1",
            "BIGQUERY_FREE_TIER_SCOPE": "unknown",
        },
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "accepted_snapshot",
        lambda _environment: (
            snapshot_document("new-snapshot") | {"quality_passed": True, "state_revision": 217}
        ),
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "latest_production_release",
        lambda: (
            {"input_snapshot_id": "old-snapshot"},
            {"status": "published", "release_id": "current-release"},
        ),
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "read_snapshot",
        lambda snapshot_id: snapshot_document(
            snapshot_id,
            file_hash=("old-data-hash" if snapshot_id == "old-snapshot" else "data-hash"),
        ),
    )
    monkeypatch.setattr(warehouse_weekly, "DATA_DIR", Path("unused"))

    def unexpected_bigquery_client(*_args, **_kwargs):
        bigquery_calls.append(object())
        raise AssertionError("BigQuery must not be called before scope is confirmed")

    monkeypatch.setattr(warehouse_weekly.bigquery, "Client", unexpected_bigquery_client)

    assert warehouse_weekly.run(dry_run=False) == 0
    assert bigquery_calls == []


def test_warehouse_schedule_skips_rebuilt_equivalent_snapshot_without_bigquery(monkeypatch) -> None:
    bigquery_calls: list[object] = []
    current = snapshot_document(
        "new-snapshot",
        repository_commit="current-commit",
        generation_id="current-generation",
    )
    equivalent = snapshot_document(
        "old-snapshot",
        repository_commit="previous-commit",
        generation_id="previous-generation",
    )
    monkeypatch.setattr(warehouse_weekly, "configure_logging", lambda: None)
    monkeypatch.setattr(warehouse_weekly, "worker_lock", lambda _path: nullcontext())
    monkeypatch.setattr(
        warehouse_weekly,
        "ready_worker_state",
        lambda: {
            "input_snapshot_id": "worker-snapshot",
            "state_counts": {"acquisition": 217},
        },
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "load_project_environment",
        lambda: {
            "GCP_PROJECT_ID": "test-project",
            "GCP_LOCATION": "europe-west1",
            "BIGQUERY_FREE_TIER_SCOPE": "single-project-confirmed",
        },
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "accepted_snapshot",
        lambda _environment: current | {"quality_passed": True, "state_revision": 217},
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "latest_production_release",
        lambda: (
            {"input_snapshot_id": "production-snapshot"},
            {"status": "published", "release_id": "current-release"},
        ),
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "read_snapshot",
        lambda _snapshot_id: equivalent,
    )

    def unexpected_bigquery_client(*_args, **_kwargs):
        bigquery_calls.append(object())
        raise AssertionError("Equivalent validated content must not trigger a rebuild")

    monkeypatch.setattr(warehouse_weekly.bigquery, "Client", unexpected_bigquery_client)

    assert warehouse_weekly.run(dry_run=False) == 0
    assert bigquery_calls == []
