from __future__ import annotations

import sys
from contextlib import nullcontext
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "warehouse_weekly.py"
_SPEC = spec_from_file_location("orbital_pulse_warehouse_weekly", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
warehouse_weekly = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = warehouse_weekly
_SPEC.loader.exec_module(warehouse_weekly)


def test_warehouse_schedule_fails_closed_without_account_scope(monkeypatch) -> None:
    bigquery_calls: list[object] = []
    monkeypatch.setattr(warehouse_weekly, "configure_logging", lambda: None)
    monkeypatch.setattr(warehouse_weekly, "worker_lock", lambda _path: nullcontext())
    monkeypatch.setattr(
        warehouse_weekly,
        "ready_worker_state",
        lambda: {"input_snapshot_id": "new-snapshot"},
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
        lambda _environment: {
            "input_snapshot_id": "new-snapshot",
            "quality_passed": True,
        },
    )
    monkeypatch.setattr(
        warehouse_weekly,
        "latest_production_release",
        lambda: (
            {"input_snapshot_id": "old-snapshot"},
            {"status": "published", "release_id": "current-release"},
        ),
    )

    def unexpected_bigquery_client(*_args, **_kwargs):
        bigquery_calls.append(object())
        raise AssertionError("BigQuery must not be called before scope is confirmed")

    monkeypatch.setattr(warehouse_weekly.bigquery, "Client", unexpected_bigquery_client)

    assert warehouse_weekly.run(dry_run=False) == 0
    assert bigquery_calls == []
