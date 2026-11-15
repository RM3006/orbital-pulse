from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from google.cloud import bigquery
from launch_atlas import cloud_worker
from launch_atlas.config import Settings
from launch_atlas.storage.local import atomic_json, digest, read_json
from launch_atlas.storage.state import StateStore
from launch_atlas.warehouse.worker_checkpoint import checkpoint, restore
from launch_atlas.windows import create_backfill_plan
from launch_atlas.worker import initialize_worker


class FakeCheckpoint:
    control = "project.space_launch_atlas_scratch.worker_proof_control"

    def __init__(self, bundle: bytes) -> None:
        self.bundle = bundle
        self.generation = digest(bundle)
        self.owner: str | None = None
        self.slot: int | None = None
        self.committed: bytes | None = None
        self.completed = False

    def claim(self, owner: str, slot: int) -> str | None:
        if self.owner is not None:
            return None
        self.owner, self.slot = owner, slot
        return self.generation

    def read(self, generation: str) -> bytes:
        assert generation == self.generation
        return self.bundle

    def commit(self, owner: str, slot: int, bundle: bytes, *, completed: bool) -> bool:
        if (owner, slot) != (self.owner, self.slot):
            return False
        self.committed, self.completed = bundle, completed
        return True


class FakeClient:
    project = "test-project"

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = [{"owner": None, "completed_slot": -1}]

    def list_rows(self, table: str, **kwargs: object) -> list[dict[str, Any]]:
        return self.rows


def test_cloud_tick_restores_runs_once_and_commits_checkpoint(
    tmp_path: Path, monkeypatch: Any
) -> None:
    original = tmp_path / "original"
    settings = Settings(data_dir=original)
    create_backfill_plan(
        StateStore(original), settings, "history", 1957, 1957, frozen_as_of=datetime.now(UTC)
    )
    initialize_worker(settings, "history")
    initial_bundle = checkpoint(original)
    proof = FakeCheckpoint(initial_bundle)
    data_dir = tmp_path / "container-data"

    monkeypatch.setattr(cloud_worker, "BigQueryCheckpointProof", lambda *args: proof)
    monkeypatch.setattr(cloud_worker, "BigQueryRawResponseArchive", lambda *args: object())
    called: list[tuple[Path, int]] = []

    def fake_tick(
        worker_settings: Settings, *, max_requests: int, raw_archive: object
    ) -> dict[str, object]:
        called.append((worker_settings.data_dir, max_requests))
        path = worker_settings.data_dir / "worker" / "state.json"
        worker = read_json(path)
        worker.update(status="waiting_weekly", state_revision=1)
        atomic_json(path, worker)
        return {"status": "waiting_weekly"}

    monkeypatch.setattr(cloud_worker, "tick_worker", fake_tick)
    summary = cloud_worker.run_checkpointed_tick(
        cast(bigquery.Client, FakeClient()),
        proof_id="0" * 32,
        location="europe-west1",
        project_id="test-project",
        dataset_prefix="space_launch_atlas",
        data_dir=data_dir,
        max_requests=1,
    )

    assert called == [(data_dir, 1)]
    assert proof.slot == 0
    assert proof.completed is True
    assert proof.committed is not None
    assert summary["status"] == "waiting_weekly"
    restored = tmp_path / "restored"
    restore(proof.committed, cloud_worker.digest(proof.committed), restored)
    assert read_json(restored / "worker" / "state.json")["state_revision"] == 1


def test_cloud_tick_commits_retry_wait_without_marking_refresh_complete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    original = tmp_path / "original"
    settings = Settings(data_dir=original)
    create_backfill_plan(
        StateStore(original), settings, "history", 1957, 1957, frozen_as_of=datetime.now(UTC)
    )
    initialize_worker(settings, "history")
    proof = FakeCheckpoint(checkpoint(original))
    data_dir = tmp_path / "container-data"

    monkeypatch.setattr(cloud_worker, "BigQueryCheckpointProof", lambda *args: proof)
    monkeypatch.setattr(cloud_worker, "BigQueryRawResponseArchive", lambda *args: object())

    def fake_tick(
        worker_settings: Settings, *, max_requests: int, raw_archive: object
    ) -> dict[str, object]:
        path = worker_settings.data_dir / "worker" / "state.json"
        worker = read_json(path)
        worker.update(status="waiting_quota", state_revision=1)
        atomic_json(path, worker)
        return {"status": "waiting_quota"}

    monkeypatch.setattr(cloud_worker, "tick_worker", fake_tick)
    summary = cloud_worker.run_checkpointed_tick(
        cast(bigquery.Client, FakeClient()),
        proof_id="1" * 32,
        location="europe-west1",
        project_id="test-project",
        dataset_prefix="space_launch_atlas",
        data_dir=data_dir,
    )

    assert proof.committed is not None
    assert proof.completed is False
    assert summary["status"] == "waiting_quota"
    restored = tmp_path / "restored"
    restore(proof.committed, cloud_worker.digest(proof.committed), restored)
    assert read_json(restored / "worker" / "state.json")["status"] == "waiting_quota"
