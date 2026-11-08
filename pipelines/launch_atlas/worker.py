"""Local, bounded scheduler ticks: initial history, catch-up, then weekly deltas.

The OS schedules ticks; durable state owns when any network work is actually due.
No cloud client is constructed here. Raw/state/export operations reuse the CLI chain.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from launch_atlas.config import Settings
from launch_atlas.pipeline import rebuild_state, run_plan
from launch_atlas.storage.exports import export_current
from launch_atlas.storage.local import LocalStore, atomic_json, read_json
from launch_atlas.storage.snapshots import create_input_snapshot
from launch_atlas.storage.state import StateStore
from launch_atlas.windows import create_refresh_plan

WEEK = timedelta(days=7)


class WorkerBusy(ValueError):
    pass


@contextmanager
def worker_lock(root: Path) -> Iterator[None]:
    """Kernel lock is released on process death; never unlink a locked inode."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "process.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WorkerBusy("Another worker tick or control command is running") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        raise ValueError("Invalid writer lock PID; inspect the lock manually")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # PID no longer exists
                return False
            if error == 5:  # Cannot inspect another process: fail closed.
                return True
            raise OSError(error, "Cannot inspect writer process")
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise OSError(ctypes.get_last_error(), "Cannot inspect writer exit code")
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _writer_available(settings: Settings) -> bool:
    path = settings.data_dir / ".writer.lock"
    if not path.exists():
        return True
    marker = path.read_bytes()
    if process_alive(int(marker)):
        return False
    # A live process never loses its marker. Re-read before recovering a dead owner.
    if path.read_bytes() != marker:
        return False
    path.unlink()
    return True


def quota_window(settings: Settings, now: datetime) -> dict[str, Any]:
    path = settings.data_dir / "quota.json"
    ledger = read_json(path) if path.exists() else {"requests": [], "retry_at": 0}
    stamps = [float(value) for value in ledger["requests"] if value > now.timestamp() - 3600]
    retry_at = float(ledger.get("retry_at") or 0)
    available = max(0, 15 - len(stamps))
    next_at = max(now.timestamp(), retry_at)
    if not available:
        next_at = max(next_at, min(stamps) + 3600)
    return {
        "available": available if retry_at <= now.timestamp() else 0,
        "next_at_utc": datetime.fromtimestamp(next_at, UTC).isoformat(),
        "consecutive_failures": int(ledger.get("consecutive_failures", 0)),
        "last_failure": ledger.get("last_failure"),
    }


def initialize_worker(settings: Settings, history_plan_id: str) -> dict[str, Any]:
    state = StateStore(settings.data_dir)
    if state.plan(history_plan_id)["plan_type"] != "backfill":
        raise ValueError("Worker requires an existing frozen backfill plan")
    if len(history_plan_id) > 40:
        raise ValueError("Use a history plan ID of at most 40 characters")
    root = settings.data_dir / "worker"
    with worker_lock(root):
        path = root / "state.json"
        if path.exists():
            previous = read_json(path)
            if previous["history_plan_id"] != history_plan_id:
                raise ValueError("Worker is already bound to another history plan")
            return previous
        worker = {
            "version": 1,
            "history_plan_id": history_plan_id,
            "active_plan_id": history_plan_id,
            "phase": "history",
            "status": "ready",
            "next_action_at_utc": datetime.now(UTC).isoformat(),
            "weekly_interval_days": 7,
            "requests_total": 0,
            "history_audit_passed": False,
            "warehouse_status": "manual_handoff_pending_final_history_checks",
        }
        atomic_json(path, worker)
        return worker


def worker_status(settings: Settings) -> dict[str, Any]:
    root = settings.data_dir / "worker"
    path = root / "state.json"
    return {
        **(read_json(path) if path.exists() else {"status": "not_initialized"}),
        "stop_requested": (root / "stop.json").exists(),
        "quota": quota_window(settings, datetime.now(UTC)),
    }


def stop_worker(settings: Settings) -> dict[str, Any]:
    atomic_json(
        settings.data_dir / "worker" / "stop.json",
        {"requested_at_utc": datetime.now(UTC).isoformat()},
    )
    return {"status": "stop_requested", "boundary": "end of current bounded invocation"}


def resume_worker(settings: Settings, reason: str) -> dict[str, Any]:
    if len(reason.strip()) < 10:
        raise ValueError("Provide an investigation/resume reason of at least 10 characters")
    root = settings.data_dir / "worker"
    with worker_lock(root), LocalStore(settings.data_dir).lock():
        worker = read_json(root / "state.json")
        path = settings.data_dir / "quota.json"
        if path.exists():
            ledger = read_json(path)
            ledger.update(consecutive_failures=0, last_failure=None)
            atomic_json(path, ledger)  # Never clear quota reservations or Retry-After.
        (root / "stop.json").unlink(missing_ok=True)
        worker.update(status="ready", next_action_at_utc=datetime.now(UTC).isoformat())
        worker["resume_review"] = {"at_utc": datetime.now(UTC).isoformat(), "reason": reason}
        atomic_json(root / "state.json", worker)
        return worker


def _audit_history(settings: Settings, worker: dict[str, Any]) -> None:
    root = settings.data_dir / "worker"
    output = root / ("audit-" + uuid4().hex[:12])
    result = rebuild_state(settings, output)
    active = read_json(settings.data_dir / "exports" / "active.json")
    expected = {name: (item["sha256"], item["rows"]) for name, item in active["files"].items()}
    actual = {
        name: (item["sha256"], item["rows"]) for name, item in result["generation"]["files"].items()
    }
    current_counts = StateStore(settings.data_dir).summary()
    semantic_tables = ("launch_current", "record_sighting", "observation_event", "state_conflict")
    if expected != actual or any(
        result["state"][name] != current_counts[name] for name in semantic_tables
    ):
        raise ValueError(
            "Full raw replay differs from accepted state/Parquet; inspect audit output"
        )
    atomic_json(root / "history-audit.json", result)
    worker.update(history_audit_passed=True, history_audit_path=str(output))


def tick_worker(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """At most eight requests per tick; quota and circuit apply across every caller."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    root = settings.data_dir / "worker"
    with worker_lock(root):
        worker = read_json(root / "state.json")

        def save(status: str, delay: timedelta = timedelta()) -> dict[str, Any]:
            worker.update(status=status, heartbeat_at_utc=datetime.now(UTC).isoformat())
            worker["next_action_at_utc"] = (now + delay).isoformat()
            atomic_json(root / "state.json", worker)
            return worker

        if (root / "stop.json").exists():
            return save("stopped")
        if worker["status"] == "blocked":
            return worker
        if now < datetime.fromisoformat(worker["next_action_at_utc"]):
            worker["heartbeat_at_utc"] = datetime.now(UTC).isoformat()
            atomic_json(root / "state.json", worker)
            return worker
        try:
            if not _writer_available(settings):
                return save("waiting_writer", timedelta(minutes=1))
            state = StateStore(settings.data_dir)
            if state.summary()["state_conflict"]:
                raise ValueError("Unresolved source conflicts require investigation")
            quota = quota_window(settings, now)
            if quota["consecutive_failures"] >= 3:
                raise ValueError("Three consecutive failed attempts; investigate raw evidence")
            if not quota["available"]:
                next_at = datetime.fromisoformat(quota["next_at_utc"]) + timedelta(seconds=5)
                return save("waiting_quota", next_at - now)
            if worker["phase"] == "refresh" and not worker.get("active_plan_id"):
                plan_id = worker["history_plan_id"] + "-w-" + uuid4().hex[:12]
                create_refresh_plan(state, settings, plan_id, frozen_as_of=now)
                worker["active_plan_id"] = plan_id
            save("running")  # Persist the selected frozen plan before any network request.
            result = run_plan(
                settings.model_copy(update={"max_requests": min(8, quota["available"])}),
                worker["active_plan_id"],
                transport=transport,
            )
            batch_path = root / "batches" / (uuid4().hex + ".json")
            atomic_json(batch_path, result)
            worker.update(
                last_batch_path=str(batch_path),
                last_result=result,
                requests_total=worker["requests_total"] + result["requests_this_invocation"],
                state_revision=state.revision(),
                state_counts=state.summary(),
            )
            if any(
                row["status"]
                not in {
                    "committed",
                    "split_into_months",
                    "budget_stopped",
                    "quota_stopped",
                    "throttled",
                    "retry_wait",
                }
                for row in result["results"]
            ):
                raise ValueError("Source/validation failure; inspect last batch and raw manifests")
            if state.summary()["state_conflict"]:
                raise ValueError("New source conflict; inspect accepted evidence")
            if result["windows_pending"] == 0 and result["windows_unreconciled"]:
                raise ValueError(
                    "Historical passes differ; another reviewed reconciliation is needed"
                )
            active_path = settings.data_dir / "exports" / "active.json"
            active = read_json(active_path) if active_path.exists() else {}
            if (
                state.summary()["launch_current"]
                and active.get("state_revision") != state.revision()
            ):
                active = export_current(settings)
            worker["last_generation_id"] = active.get("generation_id")
            if result["complete"]:
                if not state.summary()["launch_current"]:
                    raise ValueError(
                        "Completed history contains no launch records; investigate source"
                    )
                if worker["phase"] == "history":
                    worker.update(phase="refresh", active_plan_id=None)
                    return save("ready")  # Fresh catch-up before the first weekly interval.
                if not worker["history_audit_passed"]:
                    _audit_history(settings, worker)
                snapshot = create_input_snapshot(settings)
                worker.update(
                    active_plan_id=None,
                    last_successful_refresh_as_of_utc=state.plan(result["plan_id"])[
                        "frozen_as_of_utc"
                    ],
                    last_successful_refresh_at_utc=now.isoformat(),
                    input_snapshot_id=snapshot["input_snapshot_id"],
                    warehouse_status="verified_snapshot_ready_for_manual_warehouse_chain",
                )
                return save("waiting_weekly", WEEK)
            # Honor per-manifest long Retry-After as well as the source-wide ledger.
            waits = [now + timedelta(seconds=5)]
            for row in result["results"]:
                if row["status"] in {"throttled", "quota_stopped", "retry_wait"}:
                    manifest = LocalStore(settings.data_dir).manifest(row["window_id"])
                    if manifest.get("retry_at"):
                        waits.append(datetime.fromtimestamp(manifest["retry_at"], UTC))
            quota = quota_window(settings, now)
            if quota["consecutive_failures"] >= 3:
                raise ValueError("Three consecutive failed attempts; investigate raw evidence")
            if not quota["available"]:
                waits.append(datetime.fromisoformat(quota["next_at_utc"]) + timedelta(seconds=5))
            return save("waiting_quota" if not quota["available"] else "ready", max(waits) - now)
        except Exception as exc:
            # Local evidence only; never serialize exception text that may contain credentials.
            worker["failure"] = {"type": type(exc).__name__, "at_utc": now.isoformat()}
            if isinstance(exc, (ValueError, OSError)):
                worker["failure"]["reason"] = str(exc)
            save("blocked")
            raise
