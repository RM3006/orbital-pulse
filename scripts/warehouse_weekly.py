"""Guarded local scheduler for production BigQuery releases.

The Windows task invokes this once daily. It performs no BigQuery call while the
worker is stopped/incomplete or the accepted input snapshot matches production.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from google.cloud import bigquery  # noqa: E402
from launch_atlas.warehouse.reporting import BigQueryReportingStore  # noqa: E402
from launch_atlas.worker import worker_lock  # noqa: E402

DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "worker" / "state.json"
STOP_PATH = DATA_DIR / "worker" / "stop.json"
RUNS_DIR = DATA_DIR / "warehouse" / "runs"
LOG_DIR = DATA_DIR / "warehouse" / "schedule"
FREE_QUERY_BYTES = 1024**4
FREE_STORAGE_BYTES = 10 * 1024**3
RUN_QUERY_RESERVE_BYTES = 20 * 1024**3
RUN_STORAGE_RESERVE_BYTES = 1024**3
MAX_BYTES_BILLED_PER_QUERY = 100_000_000


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"warehouse-{datetime.now().astimezone():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )


def load_project_environment() -> dict[str, str]:
    """Read the non-secret project settings needed by this task."""
    environment = os.environ.copy()
    # Scope confirmation must be an explicit local project setting, never an
    # ambient variable inherited from an unrelated shell/task configuration.
    environment["BIGQUERY_FREE_TIER_SCOPE"] = "unknown"
    path = ROOT / ".env.local"
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key not in {
                "GCP_PROJECT_ID",
                "GCP_LOCATION",
                "BIGQUERY_FREE_TIER_SCOPE",
            }:
                continue
            value = value.strip().strip("\"'")
            if value:
                environment[key] = value
    if not environment.get("GCP_PROJECT_ID") or not environment.get("GCP_LOCATION"):
        raise RuntimeError("GCP_PROJECT_ID and GCP_LOCATION must be set in .env.local")
    # User ADC does not carry a default project. Set the standard library
    # variables explicitly so nested Google clients resolve the same project.
    environment["GOOGLE_CLOUD_PROJECT"] = environment["GCP_PROJECT_ID"]
    environment["GCLOUD_PROJECT"] = environment["GCP_PROJECT_ID"]
    environment["BIGQUERY_FREE_TIER_SCOPE"] = (
        environment.get("BIGQUERY_FREE_TIER_SCOPE", "unknown").strip().lower()
    )
    return environment


def free_tier_scope_confirmed(environment: dict[str, str]) -> bool:
    """Return whether the user confirmed this is the account's only BQ project."""
    return environment.get("BIGQUERY_FREE_TIER_SCOPE") == "single-project-confirmed"


def run_cli(arguments: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cli_environment = environment.copy()
    venv_scripts = str(ROOT / ".venv" / "Scripts")
    cli_environment["PATH"] = os.pathsep.join([venv_scripts, cli_environment.get("PATH", "")])
    return subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), *arguments],
        cwd=ROOT,
        env=cli_environment,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )


def latest_production_release() -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for manifest_path in RUNS_DIR.glob("*/release-manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("preview") is True:
                continue
            result_path = manifest_path.with_name("result.json")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("status") != "published":
                continue
            candidates.append((manifest_path.stat().st_mtime_ns, manifest, result))
        except (OSError, json.JSONDecodeError):
            continue
    if not candidates:
        raise RuntimeError("No successful local production release manifest is available")
    _, manifest, result = max(candidates, key=lambda item: item[0])
    return manifest, result


def accepted_snapshot(environment: dict[str, str]) -> dict[str, Any]:
    result = run_cli(["-m", "launch_atlas", "input-snapshot", "--data-dir", "data"], environment)
    if result.returncode != 0:
        raise RuntimeError("Offline input-snapshot validation failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("input-snapshot did not return valid JSON") from exc


def monthly_query_usage(client: bigquery.Client, project_id: str) -> tuple[int, int]:
    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    billed_bytes = 0
    query_jobs = 0
    jobs = client.list_jobs(
        project=project_id,
        all_users=True,
        state_filter="DONE",
        min_creation_time=month_start,
    )
    for job in jobs:
        if str(getattr(job, "job_type", "")).upper() != "QUERY":
            continue
        value = getattr(job, "total_bytes_billed", None)
        if value is None:
            properties = getattr(job, "_properties", {})
            error = (properties.get("status") or {}).get("errorResult") or {}
            reason = error.get("reason")
            processed = getattr(job, "total_bytes_processed", None)
            if processed is None and reason in {
                "accessDenied",
                "invalidQuery",
                "bytesBilledLimitExceeded",
            }:
                # These jobs failed before query execution, so they have no
                # scan to count. Unknown missing statistics remain a hard stop.
                query_jobs += 1
                continue
            raise RuntimeError(
                "A completed project query job has no billed-byte count "
                f"(state={job.state}, error_reason={reason or 'unknown'})"
            )
        billed_bytes += int(value)
        query_jobs += 1
    return billed_bytes, query_jobs


def project_storage_usage(client: bigquery.Client, project_id: str) -> tuple[int, int]:
    stored_bytes = 0
    base_tables = 0
    for dataset in client.list_datasets(project=project_id):
        for item in client.list_tables(dataset.reference):
            if str(getattr(item, "table_type", "")).upper() not in {"TABLE", "MATERIALIZED_VIEW"}:
                continue
            table = client.get_table(item.reference)
            size = getattr(table, "num_bytes", None)
            if size is None:
                raise RuntimeError("A project table has no visible storage-byte count")
            stored_bytes += int(size)
            base_tables += 1
    return stored_bytes, base_tables


def ready_worker_state() -> dict[str, Any] | None:
    if STOP_PATH.exists():
        logging.info("skip: worker stop marker is present")
        return None
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Worker state is missing or unreadable") from exc
    quota = state.get("quota", {})
    counts = state.get("state_counts", {})
    if state.get("status") != "waiting_weekly":
        logging.info("skip: worker status is %s", state.get("status", "unknown"))
        return None
    if (
        state.get("active_plan_id") is not None
        or state.get("last_result", {}).get("complete") is not True
    ):
        logging.info("skip: latest acquisition plan is not complete")
        return None
    if not state.get("history_audit_passed") or counts.get("state_conflict", 0) != 0:
        logging.info("skip: history audit or state-conflict gate failed")
        return None
    if quota.get("consecutive_failures", 0) >= 3:
        logging.info("skip: source failure circuit is open")
        return None
    if not state.get("input_snapshot_id"):
        logging.info("skip: worker has no accepted input snapshot")
        return None
    return state


def run(dry_run: bool) -> int:
    configure_logging()
    logging.info("warehouse schedule check started; dry_run=%s", dry_run)
    try:
        with worker_lock(DATA_DIR / "worker"):
            state = ready_worker_state()
            if state is None:
                return 0
            environment = load_project_environment()
            snapshot = accepted_snapshot(environment)
            snapshot_id = snapshot.get("input_snapshot_id")
            if snapshot.get("quality_passed") is not True:
                raise RuntimeError("Current input snapshot did not pass quality validation")
            if snapshot_id != state.get("input_snapshot_id"):
                raise RuntimeError("Worker and verified input-snapshot IDs do not match")
            local_manifest, local_result = latest_production_release()
            if snapshot_id == local_manifest.get("input_snapshot_id"):
                logging.info(
                    "skip: accepted snapshot already matches production release %s",
                    local_result.get("release_id"),
                )
                return 0
            if not free_tier_scope_confirmed(environment):
                logging.warning(
                    "skip: monthly free usage is account-wide; set "
                    "BIGQUERY_FREE_TIER_SCOPE=single-project-confirmed only after "
                    "confirming no other BigQuery project uses this billing account"
                )
                return 0
            if dry_run:
                logging.info(
                    "dry-run: would validate project monthly usage and publish changed snapshot %s",
                    snapshot_id,
                )
                return 0

            project_id = environment["GCP_PROJECT_ID"]
            location = environment["GCP_LOCATION"]
            client = bigquery.Client(project=project_id, location=location)
            logging.info(
                "BigQuery preflight configured project=%s client_project=%s location=%s",
                project_id,
                client.project,
                location,
            )
            month_bytes, month_jobs = monthly_query_usage(client, project_id)
            storage_bytes, storage_tables = project_storage_usage(client, project_id)
            logging.info(
                "usage: billed_bytes=%d jobs=%d stored_bytes=%d tables=%d",
                month_bytes,
                month_jobs,
                storage_bytes,
                storage_tables,
            )
            if month_bytes + RUN_QUERY_RESERVE_BYTES > FREE_QUERY_BYTES:
                logging.warning(
                    "skip: 20 GiB run reserve would cross the 1 TiB monthly query allowance"
                )
                return 0
            if storage_bytes + RUN_STORAGE_RESERVE_BYTES > FREE_STORAGE_BYTES:
                logging.warning("skip: 1 GiB run reserve would cross the 10 GiB storage allowance")
                return 0

            reporting = BigQueryReportingStore(
                client,
                project_id,
                "space_launch_atlas_reporting",
                location,
                candidate_dataset="space_launch_atlas_scratch",
                maximum_bytes_billed=MAX_BYTES_BILLED_PER_QUERY,
            )
            active = reporting.active_release_manifest()
            if active is None:
                raise RuntimeError("No validated production release pointer is available")
            if active.input_snapshot_id == snapshot_id:
                logging.info("skip: accepted snapshot is already active in BigQuery")
                return 0

            logging.info(
                "publishing changed snapshot with expected active release %s", active.release_id
            )
            result = run_cli(
                [
                    "-m",
                    "launch_atlas",
                    "warehouse-run",
                    "--data-dir",
                    "data",
                    "--publish",
                    "--expected-active",
                    active.release_id,
                ],
                environment,
            )
            if result.returncode != 0:
                logging.error(
                    "warehouse pipeline failed (exit %d); active release gate remains in force",
                    result.returncode,
                )
                if result.stdout.strip():
                    logging.error("pipeline output: %s", result.stdout.strip()[-2000:])
                return result.returncode or 1
            try:
                published = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "warehouse pipeline returned invalid JSON after completion"
                ) from exc
            jobs = published.get("query_jobs", [])
            billed = sum(int(job.get("bytes_billed") or 0) for job in jobs)
            logging.info(
                "published release=%s snapshot=%s billed_bytes=%d query_jobs=%d run_dir=%s",
                published.get("release_id"),
                published.get("input_snapshot_id") or snapshot_id,
                billed,
                len(jobs),
                published.get("run_dir"),
            )
            if billed > RUN_QUERY_RESERVE_BYTES or len(jobs) > 200:
                logging.error(
                    "published run exceeded reserve assumptions; suspend scheduling for review"
                )
                return 1
            return 0
    except Exception as exc:
        logging.error("warehouse schedule stopped safely: %s: %s", type(exc).__name__, exc)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run local readiness/snapshot checks without BigQuery calls",
    )
    arguments = parser.parse_args()
    return run(arguments.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
