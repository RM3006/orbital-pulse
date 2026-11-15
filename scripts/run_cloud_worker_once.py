"""Seed a short-lived checkpoint, execute Cloud Run once, and reconcile its raw rows."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession, Request
from google.cloud import bigquery
from launch_atlas.config import Settings
from launch_atlas.storage.local import LocalStore, atomic_json, digest, read_json
from launch_atlas.warehouse.raw_json import RawArchiveTarget, make_raw_response_row
from launch_atlas.warehouse.worker_checkpoint import BigQueryCheckpointProof, checkpoint, restore
from launch_atlas.worker import quota_window

ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def google_request(
    session: AuthorizedSession, method: str, url: str, **kwargs: object
) -> dict[str, Any]:
    response = session.request(method, url, timeout=30, **kwargs)
    if not response.ok:
        try:
            message = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = response.text[:300]
        raise RuntimeError(f"Google API returned HTTP {response.status_code}: {message}")
    return response.json() if response.content else {}


def cloud_run_overrides(proof_id: str, max_requests: int) -> dict[str, Any]:
    """Set per-execution checkpoint and LL2 request cap without changing the Job."""
    if not 1 <= max_requests <= 8:
        raise ValueError("A Cloud Run worker tick must be limited to 1-8 requests")
    return {
        "overrides": {
            "containerOverrides": [
                {
                    "name": "worker",
                    "env": [
                        {"name": "CHECKPOINT_PROOF_ID", "value": proof_id},
                        {"name": "WORKER_MAX_REQUESTS", "value": str(max_requests)},
                    ],
                }
            ],
            "taskCount": 1,
            "timeout": "600s",
        }
    }


def prepare_due_checkpoint(data_dir: Path, now: datetime) -> tuple[bytes, int]:
    """Make an isolated due copy; the local worker and its schedule remain untouched."""
    original = checkpoint(data_dir)
    with tempfile.TemporaryDirectory(prefix="sla-cloud-seed-") as temporary:
        seed_dir = Path(temporary) / "data"
        restore(original, digest(original), seed_dir)
        state_path = seed_dir / "worker" / "state.json"
        state = read_json(state_path)
        if (
            state.get("status") != "waiting_weekly"
            or state.get("phase") != "refresh"
            or not state.get("history_audit_passed")
        ):
            raise RuntimeError("The local worker is not at the verified weekly-refresh checkpoint")
        settings = Settings.from_env(data_dir=seed_dir)
        available = int(quota_window(settings, now)["available"])
        if available < 1:
            raise RuntimeError("The shared 15-per-hour request budget has no available call")
        state["next_action_at_utc"] = now.isoformat()
        state["one_off_cloud_test_due_at_utc"] = now.isoformat()
        atomic_json(state_path, state)
        return checkpoint(seed_dir), available


def expected_raw_rows(data_dir: Path, started_at: datetime) -> dict[str, str]:
    local = LocalStore(data_dir)
    expected: dict[str, str] = {}
    for manifest_path in (data_dir / "raw" / "ll2").glob("*/manifest.json"):
        manifest = read_json(manifest_path)
        for number, attempt in enumerate(manifest.get("attempts", []), start=1):
            observed = attempt.get("observed_at_utc")
            if not observed:
                continue
            observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00")).astimezone(UTC)
            if observed_at < started_at:
                continue
            artifact = attempt.get("artifact")
            if not artifact:
                continue
            body = local.read_body(artifact, attempt["sha256"])
            row = make_raw_response_row(
                manifest["run_id"],
                int(attempt.get("request_number", number)),
                str(manifest.get("kind", "live")),
                attempt,
                body,
            )
            expected[str(row["response_id"])] = str(row["body_sha256"])
    return expected


def verify_raw_archive(
    client: bigquery.Client,
    target: RawArchiveTarget,
    expected: dict[str, str],
) -> tuple[int, int]:
    if not expected:
        raise RuntimeError("The Cloud Run checkpoint contains no new HTTP responses")
    query = f"""
      SELECT response_id, COUNT(*) AS copies,
             ANY_VALUE(body_sha256) AS recorded_sha,
             ANY_VALUE(TO_HEX(SHA256(response_body))) AS computed_sha
      FROM `{target.table_id}`
      WHERE response_id IN UNNEST(@response_ids)
      GROUP BY response_id
    """
    config = bigquery.QueryJobConfig(
        maximum_bytes_billed=2 * 1024**3,
        query_parameters=[bigquery.ArrayQueryParameter("response_ids", "STRING", list(expected))],
    )
    job = client.query(query, location=target.location, job_config=config)
    actual = {str(row["response_id"]): row for row in job.result(timeout=120)}
    if set(actual) != set(expected):
        raise RuntimeError("BigQuery raw response IDs do not match the Cloud Run local manifest")
    for response_id, row in actual.items():
        if row["copies"] != 1 or row["recorded_sha"] != expected[response_id]:
            raise RuntimeError("BigQuery has a missing, duplicate, or mismatched raw response")
        if row["computed_sha"].lower() != expected[response_id]:
            raise RuntimeError("BigQuery response BYTES do not match the recorded SHA-256")
    return len(actual), int(job.total_bytes_billed or 0)


def poll_execution(
    session: AuthorizedSession, execution_name: str, max_wait_seconds: int
) -> tuple[dict[str, Any], bool]:
    url = f"https://run.googleapis.com/v2/{execution_name}"
    deadline = time.monotonic() + max_wait_seconds
    execution: dict[str, Any] = {}
    while time.monotonic() < deadline:
        execution = google_request(session, "GET", url)
        completion = execution.get("completionTime")
        condition = next(
            (item for item in execution.get("conditions", []) if item.get("type") == "Completed"),
            {},
        )
        finished = bool(completion) or condition.get("state") in {
            "CONDITION_SUCCEEDED",
            "CONDITION_FAILED",
        }
        if finished:
            return execution, True
        time.sleep(5)
    return execution, False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local")
    parser.add_argument("--job", default="sla-ll2-worker")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--max-wait-seconds", type=int, default=780)
    parser.add_argument("--max-requests", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--expect-requests", type=int, choices=range(1, 9), default=None)
    parser.add_argument("--expected-execution-count", type=int, default=0)
    parser.add_argument("--confirm-live-ll2", action="store_true", required=True)
    args = parser.parse_args()
    load_env_file(args.env_file)
    project = os.environ["GCP_PROJECT_ID"]
    location = os.environ["GCP_LOCATION"]
    dataset_prefix = os.getenv("DBT_SOURCE_DATASET", "space_launch_atlas_source").removesuffix(
        "_source"
    )
    data_dir = (args.data_dir or Path(os.getenv("LOCAL_DATA_DIR", "data"))).resolve()
    now = datetime.now(UTC)
    prepared, quota_available = prepare_due_checkpoint(data_dir, now)
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    session = AuthorizedSession(credentials)
    client = bigquery.Client(project=project, credentials=credentials)
    job_name = f"projects/{project}/locations/{location}/jobs/{args.job}"
    job = google_request(session, "GET", f"https://run.googleapis.com/v2/{job_name}")
    if job.get("reconciling"):
        raise RuntimeError("Cloud Run job configuration is still reconciling")
    execution_count = int(job.get("executionCount", 0))
    if execution_count != args.expected_execution_count:
        raise RuntimeError(
            "Cloud Run execution count changed: expected "
            f"{args.expected_execution_count}, found {execution_count}; refusing to trigger"
        )
    if int(job.get("runningCount", 0)) != 0:
        raise RuntimeError("A Cloud Run execution is already active; refusing to trigger another")
    permission_check = google_request(
        session,
        "POST",
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:testIamPermissions",
        json={"permissions": ["run.jobs.runWithOverrides"]},
    )
    if "run.jobs.runWithOverrides" not in permission_check.get("permissions", []):
        raise RuntimeError(
            "The signed-in user cannot run this job with its one-off checkpoint override"
        )

    proof = BigQueryCheckpointProof(client, location)
    tables_created = False
    run_submission_started = False
    run_accepted = False
    terminal = False
    verified = False
    try:
        proof.create_scratch_tables()
        tables_created = True
        seed_generation = digest(prepared)
        proof.seed(prepared)
        started_at = datetime.now(UTC)
        run_submission_started = True
        response = session.post(
            f"https://run.googleapis.com/v2/{job_name}:run",
            json=cloud_run_overrides(proof.proof_id, args.max_requests),
            timeout=30,
        )
        if not response.ok:
            run_submission_started = response.status_code >= 500
            if run_submission_started:
                raise RuntimeError(
                    "Cloud Run returned HTTP "
                    f"{response.status_code}; execution outcome is uncertain"
                )
            raise RuntimeError(
                f"Cloud Run rejected the one-off execution (HTTP {response.status_code})"
            )
        run_accepted = True
        operation = response.json()
        operation_name = operation.get("name")
        run_deadline = time.monotonic() + args.max_wait_seconds
        while not operation.get("done") and operation_name and time.monotonic() < run_deadline:
            time.sleep(2)
            operation = google_request(
                session, "GET", f"https://run.googleapis.com/v2/{operation_name}"
            )
        if not operation.get("done"):
            raise TimeoutError(
                "Cloud Run has not returned a terminal operation; checkpoint tables retained"
            )
        if operation.get("error"):
            raise RuntimeError(
                "Cloud Run could not start the execution; inspect its operation before retrying"
            )
        execution_name = (operation.get("response") or {}).get("name")
        if not execution_name:
            updated_job = google_request(
                session, "GET", f"https://run.googleapis.com/v2/{job_name}"
            )
            execution_name = (updated_job.get("latestCreatedExecution") or {}).get("name")
        if not execution_name:
            raise RuntimeError(
                "Cloud Run accepted the request but did not expose an execution name"
            )
        remaining = max(1, int(run_deadline - time.monotonic()))
        execution, terminal = poll_execution(session, execution_name, remaining)
        if not terminal:
            raise TimeoutError("Cloud Run execution is not terminal; checkpoint tables retained")
        if int(execution.get("succeededCount", 0)) != 1 or int(execution.get("failedCount", 0)):
            log_uri = execution.get("logUri")
            raise RuntimeError(
                f"Cloud Run task failed; inspect execution logs: {log_uri or 'not available'}"
            )

        control = list(client.list_rows(proof.control, max_results=2, timeout=30))
        if (
            len(control) != 1
            or control[0]["owner"] is not None
            or control[0]["active_slot"] is not None
        ):
            raise RuntimeError("The worker did not release its checkpoint ownership after the tick")
        generation = str(control[0]["generation"])
        if generation == seed_generation:
            raise RuntimeError("The worker did not commit its updated checkpoint")
        final_bundle = proof.read(generation)
        with tempfile.TemporaryDirectory(prefix="sla-cloud-result-") as temporary:
            final_dir = Path(temporary) / "data"
            restore(final_bundle, generation, final_dir)
            worker = read_json(final_dir / "worker" / "state.json")
            worker_status = worker.get("status")
            if worker_status not in {"waiting_weekly", "ready", "waiting_quota"}:
                raise RuntimeError(
                    f"The worker did not reach a safe resumable checkpoint (status={worker_status})"
                )
            refresh_complete = worker_status == "waiting_weekly"
            completed_slot = int(control[0]["completed_slot"])
            if completed_slot != (0 if refresh_complete else -1):
                raise RuntimeError("The completed-slot marker does not match worker status")
            request_count = int(
                (worker.get("last_result") or {}).get("requests_this_invocation", 0)
            )
            if args.expect_requests is not None and request_count != args.expect_requests:
                raise RuntimeError(
                    f"Expected {args.expect_requests} LL2 requests, worker recorded {request_count}"
                )
            expected = expected_raw_rows(final_dir, started_at)
            target = RawArchiveTarget(project, dataset_prefix, location)
            matched, billed = verify_raw_archive(client, target, expected)
            result = worker.get("last_result") or {}
            verified = True
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "execution": execution_name,
                        "worker_status": worker_status,
                        "refresh_complete": refresh_complete,
                        "next_action_at_utc": worker.get("next_action_at_utc"),
                        "state_revision": worker.get("state_revision"),
                        "requests_this_invocation": result.get("requests_this_invocation"),
                        "max_requests_for_tick": args.max_requests,
                        "raw_response_rows_matched": matched,
                        "body_sha256_verified": True,
                        "verification_query_bytes_billed": billed,
                        "seed_quota_available": quota_available,
                        "weekly_scheduler_created": False,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    finally:
        # Retain failed-run evidence for review; the two-hour table TTL removes it.
        if tables_created and not run_submission_started:
            proof.delete_scratch_tables()
        elif tables_created and run_accepted and terminal and verified:
            proof.delete_scratch_tables()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "detail": str(exc)[:400]}
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
