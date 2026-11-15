from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.cloud import bigquery
from launch_atlas.clients.ll2 import LL2Client
from launch_atlas.config import Settings
from launch_atlas.storage.local import digest, read_json
from launch_atlas.storage.state import StateStore
from launch_atlas.warehouse.worker_checkpoint import BigQueryCheckpointProof, checkpoint, restore
from launch_atlas.windows import create_backfill_plan
from launch_atlas.worker import initialize_worker, tick_worker


def initialize(root: Path, now: datetime) -> Settings:
    settings = Settings(data_dir=root)
    create_backfill_plan(StateStore(root), settings, "test-history", 1957, 1957, frozen_as_of=now)
    initialize_worker(settings, "test-history")
    return settings


def test_checkpoint_restores_sqlite_and_rejects_corruption(tmp_path: Path) -> None:
    original = tmp_path / "source"
    initialize(original, datetime.now(UTC))
    bundle = checkpoint(original)
    target = tmp_path / "destination"
    with pytest.raises(ValueError, match="SHA-256"):
        restore(bundle + b"bad", digest(bundle), target)
    assert not target.exists()
    restore(bundle, digest(bundle), target)
    assert StateStore(target).plan("test-history")["plan_type"] == "backfill"
    assert read_json(target / "worker/state.json")["active_plan_id"] == "test-history"
    with pytest.raises(ValueError, match="empty"):
        restore(bundle, digest(bundle), target)


class SimulatedExecutorLoss(BaseException):
    pass


@pytest.mark.integration
def test_bq_resume(
    tmp_path: Path, records: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    if (
        os.getenv("RUN_BIGQUERY_INTEGRATION") != "1"
        or os.getenv("BIGQUERY_FREE_TIER_SCOPE") != "single-project-confirmed"
    ):
        pytest.skip("Opt into the bounded BigQuery scratch proof after checking account headroom")
    project, location = os.environ["GCP_PROJECT_ID"], os.environ["GCP_LOCATION"]
    history = json.loads((Path(__file__).parent / "fixtures/ll2_history_1957.json").read_text())[
        "results"
    ]
    clock = datetime.now(UTC) + timedelta(seconds=2)
    monkeypatch.setattr(
        "launch_atlas.pipeline.LL2Client", partial(LL2Client, clock=lambda: clock.timestamp())
    )
    monkeypatch.setattr(
        "launch_atlas.pipeline.time", SimpleNamespace(time=lambda: clock.timestamp())
    )
    weekly = False
    throttle = True
    offsets: list[int] = []
    requests: list[str] = []

    def ll2(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.params.get("net__gte", "").startswith("1957"):
            rows = history
        elif "last_updated__gte" in request.url.params:
            rows = []
        elif request.url.path.endswith("upcoming/"):
            rows = [records[-1]]
        elif weekly:
            offset = int(request.url.params.get("offset", "0"))
            offsets.append(offset)
            if offset == 1 and throttle:
                return httpx.Response(429, headers={"Retry-After": "600"})
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "next": str(request.url.copy_set_param("offset", "1")) if not offset else None,
                    "results": [records[2 + offset]],
                },
            )
        else:
            rows = [records[2]]
        return httpx.Response(200, json={"count": len(rows), "next": None, "results": rows})

    transport = httpx.MockTransport(ll2)
    with bigquery.Client(project=project, location=location) as client:
        proof = BigQueryCheckpointProof(client, location)
        with proof.scratch():
            with TemporaryDirectory(dir=tmp_path) as directory:
                settings = initialize(Path(directory), clock)
                assert tick_worker(settings, now=clock, transport=transport)["phase"] == "refresh"
                clock += timedelta(seconds=6)
                seed = tick_worker(settings, now=clock, transport=transport)
                assert seed["status"] == "waiting_weekly"
                initial_bytes = checkpoint(settings.data_dir)
                proof.seed(initial_bytes)
                initial_size = len(initial_bytes)
                del initial_bytes
            assert not Path(directory).exists()

            # Two independently submitted conditional DML jobs compete for one slot.
            with ThreadPoolExecutor(max_workers=2) as pool:
                claims = list(pool.map(lambda owner: proof.claim(owner, 1), ("first", "contender")))
            assert sum(value is not None for value in claims) == 1
            owner, generation = next(
                (name, value)
                for name, value in zip(("first", "contender"), claims, strict=True)
                if value is not None
            )
            assert proof.claim("overlap", 2) is None
            clock = datetime.fromisoformat(seed["next_action_at_utc"]) + timedelta(seconds=1)
            weekly = True
            with TemporaryDirectory(dir=tmp_path) as directory:
                root = Path(directory)
                restore(proof.read(generation), generation, root)
                partial_result = tick_worker(
                    Settings(data_dir=root), now=clock, transport=transport
                )
                assert offsets == [0, 1]
                assert partial_result["active_plan_id"]
                assert partial_result["status"] == "waiting_quota"
                ledger = read_json(root / "quota.json")
                assert len(ledger["requests"]) == 2
                assert ledger["retry_at"] > clock.timestamp()
                saved = checkpoint(root)
                partial_size = len(saved)
                assert not proof.commit("stale_owner", 1, saved, completed=True)
                assert proof.commit(owner, 1, saved, completed=False)
                del saved
            assert not Path(directory).exists()

            # A new executor has only the cloud pointer and an empty filesystem.
            with bigquery.Client(project=project, location=location) as new_client:
                restarted = BigQueryCheckpointProof(new_client, location, proof.proof_id)
                generation = restarted.claim("replacement", 1)
                assert generation is not None
                root = tmp_path / "replacement"
                restore(restarted.read(generation), generation, root)
                assert read_json(root / "quota.json") == ledger
                assert (
                    read_json(root / "worker/state.json")["active_plan_id"]
                    == partial_result["active_plan_id"]
                )
                settings = Settings(data_dir=root)
                count = len(requests)
                tick_worker(settings, now=clock + timedelta(seconds=1), transport=transport)
                assert len(requests) == count
                clock = datetime.fromisoformat(partial_result["next_action_at_utc"]) + timedelta(
                    seconds=1
                )
                throttle = False
                completed = tick_worker(settings, now=clock, transport=transport)
                assert completed["status"] == "waiting_weekly"
                assert offsets == [0, 1, 1]
                assert StateStore(root).summary()["launch_current"] >= 5
                assert (root / "snapshots" / f"{completed['input_snapshot_id']}.json").is_file()
                final = checkpoint(root)
                assert restarted.commit("replacement", 1, final, completed=True)
                assert restarted.claim("duplicate", 1) is None
                assert restarted.claim("late_delivery", 0) is None

                generation = restarted.claim("lost_executor", 2)
                assert generation is not None and generation == digest(final)
                with TemporaryDirectory(dir=tmp_path) as directory:
                    root = Path(directory)
                    restore(restarted.read(generation), generation, root)
                    clock = datetime.fromisoformat(completed["next_action_at_utc"]) + timedelta(
                        seconds=1
                    )

                    def disappear(_: httpx.Request) -> httpx.Response:
                        raise SimulatedExecutorLoss()

                    with pytest.raises(SimulatedExecutorLoss):
                        tick_worker(
                            Settings(data_dir=root),
                            now=clock,
                            transport=httpx.MockTransport(disappear),
                        )
                assert not Path(directory).exists()
                assert restarted.claim("unexpected_retry", 2) is None
                assert restarted.claim("new_week", 3) is None
                print(
                    json.dumps(
                        {
                            "checkpoint_bytes": [initial_size, partial_size, len(final)],
                            "control_query_bytes_billed": proof.billed_bytes
                            + restarted.billed_bytes,
                            "committed_weekly_launch_count": completed["state_counts"][
                                "launch_current"
                            ],
                            "recent_offsets": offsets,
                            "real_ll2_requests": 0,
                        }
                    )
                )
