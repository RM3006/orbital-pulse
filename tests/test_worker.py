import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from launch_atlas.cli import main
from launch_atlas.clients.ll2 import LL2Client, PauseRun, SourceError
from launch_atlas.config import Settings
from launch_atlas.storage.local import LocalStore, atomic_json, read_json
from launch_atlas.storage.state import StateStore
from launch_atlas.windows import create_backfill_plan
from launch_atlas.worker import (
    WorkerBusy,
    initialize_worker,
    process_alive,
    resume_worker,
    stop_worker,
    tick_worker,
    worker_lock,
    worker_status,
)


def setup_worker(root: Path) -> tuple[Settings, datetime]:
    settings = Settings(data_dir=root)
    now = datetime.now(UTC)
    create_backfill_plan(StateStore(root), settings, "test-history", 1957, 1957, frozen_as_of=now)
    initialize_worker(settings, "test-history")
    return settings, now + timedelta(seconds=1)


def test_real_chain_history_catchup_audit_weekly_and_corrected_old_record(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings, now = setup_worker(tmp_path)
    history = json.loads((Path(__file__).parent / "fixtures/ll2_history_1957.json").read_text())[
        "results"
    ]
    changed = False
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.params.get("net__gte", "").startswith("1957"):
            rows = history
        elif "last_updated__gte" in request.url.params:
            rows = (
                []
                if not changed
                else [
                    dict(
                        history[0],
                        name="Corrected historical name",
                        last_updated="2026-11-08T18:00:00Z",
                    )
                ]
            )
        elif request.url.path.endswith("upcoming/"):
            rows = [records[-1]]
        else:
            rows = [records[2]]
        return httpx.Response(200, json={"count": len(rows), "next": None, "results": rows})

    transport = httpx.MockTransport(handler)
    history_done = tick_worker(settings, now=now, transport=transport)
    assert history_done["phase"] == "refresh"
    assert len(calls) == 2
    done = tick_worker(settings, now=now + timedelta(seconds=6), transport=transport)
    assert done["status"] == "waiting_weekly"
    assert done["history_audit_passed"] is True
    assert (tmp_path / "snapshots" / (done["input_snapshot_id"] + ".json")).exists()
    assert len(read_json(tmp_path / "exports/active.json")["files"]) == 11
    assert len(calls) == 5
    # A separate invocation/restart before due time makes no request.
    tick_worker(settings, now=now + timedelta(days=6), transport=transport)
    assert len(calls) == 5
    first_plan = StateStore(tmp_path).plan(done["last_result"]["plan_id"])
    changed = True
    next_result = tick_worker(settings, now=now + timedelta(days=7, seconds=7), transport=transport)
    assert next_result["status"] == "waiting_weekly"
    correction_url = next(url for url in calls[5:] if "last_updated__gte" in url)
    correction_request = httpx.Request("GET", correction_url)
    expected_start = datetime.fromisoformat(first_plan["frozen_as_of_utc"]) - timedelta(
        minutes=5, hours=48
    )
    assert (
        datetime.fromisoformat(correction_request.url.params["last_updated__gte"]) == expected_start
    )
    assert StateStore(tmp_path).summary()["launch_current"] == 5
    assert any(
        record[0]["name"] == "Corrected historical name"
        for record in StateStore(tmp_path).current_records()
    )


def test_quota_wait_and_resume_from_saved_offset_without_repeating_page(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings, now = setup_worker(tmp_path)
    stamps = [time.time()] * 14
    atomic_json(tmp_path / "quota.json", {"requests": stamps, "retry_at": 0})
    offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "count": 2,
                "next": str(request.url.copy_set_param("offset", "1")) if not offset else None,
                "results": [records[offset]],
            },
        )

    transport = httpx.MockTransport(handler)
    result = tick_worker(settings, now=now, transport=transport)
    assert result["status"] == "waiting_quota"
    assert offsets == [0]
    tick_worker(settings, now=now + timedelta(seconds=10), transport=transport)
    assert offsets == [0]
    # Simulate expiration in the durable ledger, leaving the partial manifest intact.
    ledger = read_json(tmp_path / "quota.json")
    ledger["requests"] = [stamp - 3601 for stamp in ledger["requests"]]
    atomic_json(tmp_path / "quota.json", ledger)
    result = tick_worker(settings, now=now + timedelta(hours=1, seconds=10), transport=transport)
    assert result["phase"] == "refresh"
    assert offsets == [0, 1, 0, 1]
    assert StateStore(tmp_path).summary()["launch_current"] == 2


def test_three_failures_across_clients_latch_and_resume_preserves_quota(tmp_path: Path) -> None:
    settings, _ = setup_worker(tmp_path)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline failure", request=request)

    store = LocalStore(tmp_path)
    url = StateStore(tmp_path).plan_windows("test-history")[0]["next_url"]
    for attempt in range(3):
        client = LL2Client(
            settings.model_copy(update={"max_requests": 1}),
            store,
            transport=httpx.MockTransport(handler),
            sleep=lambda _: None,
        )
        try:
            with pytest.raises(SourceError if attempt == 2 else PauseRun):
                client.fetch(url, lambda *args: None)
        finally:
            client.close()
    assert calls == 3
    with pytest.raises(ValueError, match="Three consecutive"):
        tick_worker(
            settings,
            now=datetime.now(UTC) + timedelta(seconds=1),
            transport=httpx.MockTransport(handler),
        )
    assert worker_status(settings)["status"] == "blocked"
    reservations = read_json(tmp_path / "quota.json")["requests"]
    resumed = resume_worker(settings, "DNS recovered and raw attempts reviewed")
    assert resumed["status"] == "ready"
    assert read_json(tmp_path / "quota.json")["requests"] == reservations
    assert calls == 3


def test_retry_after_persists_even_on_third_failure(tmp_path: Path) -> None:
    settings, now = setup_worker(tmp_path)
    atomic_json(tmp_path / "quota.json", {"requests": [], "retry_at": 0, "consecutive_failures": 2})
    transport = httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(ValueError):
        tick_worker(settings, now=now, transport=transport)
    ledger = read_json(tmp_path / "quota.json")
    assert ledger["consecutive_failures"] == 3
    assert ledger["retry_at"] > time.time() + 3500
    resume_worker(settings, "Reviewed throttling; retaining server Retry-After")
    assert read_json(tmp_path / "quota.json")["retry_at"] == ledger["retry_at"]
    status = tick_worker(settings, now=now + timedelta(seconds=10), transport=transport)
    assert status["status"] == "waiting_quota"


def test_stop_live_writer_and_dead_writer_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]
) -> None:
    settings, now = setup_worker(tmp_path)
    assert process_alive(os.getpid())
    marker = tmp_path / ".writer.lock"
    marker.write_text(str(os.getpid()))
    assert tick_worker(settings, now=now)["status"] == "waiting_writer"
    assert marker.exists()
    stop_worker(settings)
    assert tick_worker(settings, now=now + timedelta(minutes=2))["status"] == "stopped"
    marker.unlink()
    resume_worker(settings, "Operator restarted after inspecting writer")
    marker.write_text("999999")
    monkeypatch.setattr("launch_atlas.worker.process_alive", lambda _: False)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"count": 1, "next": None, "results": records[:1]})
    )
    assert (
        tick_worker(settings, now=now + timedelta(minutes=3), transport=transport)["phase"]
        == "refresh"
    )
    assert not marker.exists()


def test_worker_kernel_lock_released_after_process_crash(tmp_path: Path) -> None:
    with worker_lock(tmp_path):
        with pytest.raises(WorkerBusy):
            with worker_lock(tmp_path):
                pass
    code = (
        "from pathlib import Path; import os; from launch_atlas.worker import worker_lock; "
        "lock=worker_lock(Path(__import__('sys').argv[1])); lock.__enter__(); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True, timeout=20)
    with worker_lock(tmp_path):
        pass


def test_worker_cli_offline_status_stop_and_idempotent_init(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, _ = setup_worker(tmp_path)
    before = initialize_worker(settings, "test-history")
    assert initialize_worker(settings, "test-history") == before
    assert main(["worker-status", "--data-dir", str(tmp_path)]) == 0
    assert '"phase": "history"' in capsys.readouterr().out
    assert main(["worker-stop", "--data-dir", str(tmp_path)]) == 0
    assert worker_status(settings)["stop_requested"]


def test_invalid_candidate_preserves_last_good_export(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings, now = setup_worker(tmp_path)
    good = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"count": 1, "next": None, "results": records[:1]})
    )
    assert tick_worker(settings, now=now, transport=good)["phase"] == "refresh"
    previous = read_json(tmp_path / "exports/active.json")
    broken = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"count": 1, "next": None, "results": [dict(records[2], id="invalid")]}
        )
    )
    with pytest.raises(ValueError):
        tick_worker(settings, now=now + timedelta(seconds=10), transport=broken)
    assert worker_status(settings)["status"] == "blocked"
    assert read_json(tmp_path / "exports/active.json") == previous


def test_disagreeing_history_passes_stop_before_weekly_switch(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings, now = setup_worker(tmp_path)
    calls = []

    def handler(_: httpx.Request) -> httpx.Response:
        record = records[len(calls)]
        calls.append(record["id"])
        return httpx.Response(200, json={"count": 1, "next": None, "results": [record]})

    with pytest.raises(ValueError, match="Historical passes differ"):
        tick_worker(settings, now=now, transport=httpx.MockTransport(handler))
    status = worker_status(settings)
    assert status["status"] == "blocked"
    assert status["phase"] == "history"
    assert status["last_result"]["windows_unreconciled"] == 1
    assert tick_worker(settings, now=now + timedelta(days=7))["status"] == "blocked"
    assert len(calls) == 2
