"""One-task Cloud Run entry point; local development and imports need no credentials."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from google.cloud import bigquery

from launch_atlas.config import Settings
from launch_atlas.storage.local import digest, read_json
from launch_atlas.warehouse.raw_json import BigQueryRawResponseArchive, RawArchiveTarget
from launch_atlas.warehouse.worker_checkpoint import (
    BigQueryCheckpointProof,
    checkpoint,
    restore,
)
from launch_atlas.worker import tick_worker


def run_checkpointed_tick(
    client: bigquery.Client,
    *,
    proof_id: str,
    location: str,
    project_id: str,
    dataset_prefix: str,
    data_dir: Path,
    max_requests: int = 8,
) -> dict[str, object]:
    """Claim, restore, run one bounded worker tick, and commit its new checkpoint."""
    proof = BigQueryCheckpointProof(client, location, proof_id)
    rows = list(client.list_rows(proof.control, max_results=2, timeout=30))
    if len(rows) != 1 or rows[0]["owner"] is not None:
        raise RuntimeError("Checkpoint is missing, duplicated, or already owned")
    slot = int(rows[0]["completed_slot"]) + 1
    owner = uuid4().hex
    generation = proof.claim(owner, slot)
    if generation is None:
        raise RuntimeError("Another execution claimed this worker checkpoint")

    bundle = proof.read(generation)
    restore(bundle, generation, data_dir)
    settings = Settings.from_env(data_dir=data_dir)
    target = RawArchiveTarget(project_id, dataset_prefix, location)
    archive = BigQueryRawResponseArchive(client, target)
    result = tick_worker(settings, max_requests=max_requests, raw_archive=archive)
    updated = checkpoint(data_dir)
    complete = result.get("status") == "waiting_weekly"
    if not proof.commit(owner, slot, updated, completed=complete):
        raise RuntimeError("Worker checkpoint commit lost its ownership fence")
    worker = read_json(data_dir / "worker" / "state.json")
    summary = {
        "status": worker.get("status"),
        "phase": worker.get("phase"),
        "state_revision": worker.get("state_revision"),
        "requests_this_invocation": (worker.get("last_result") or {}).get(
            "requests_this_invocation", 0
        ),
        "input_snapshot_id": worker.get("input_snapshot_id"),
        "checkpoint_generation": digest(updated),
        "slot": slot,
    }
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def main() -> int:
    """Cloud Run entry point; missing checkpoint parameters fail before any LL2 call."""
    required = ("GCP_PROJECT_ID", "GCP_LOCATION", "CHECKPOINT_PROOF_ID")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        print(json.dumps({"status": "blocked", "missing": missing}), file=sys.stderr)
        return 2
    project_id = os.environ["GCP_PROJECT_ID"]
    location = os.environ["GCP_LOCATION"]
    dataset_prefix = os.getenv("BIGQUERY_DATASET_PREFIX", "space_launch_atlas")
    try:
        max_requests = int(os.getenv("WORKER_MAX_REQUESTS", "8"))
        client = bigquery.Client(project=project_id)
        run_checkpointed_tick(
            client,
            proof_id=os.environ["CHECKPOINT_PROOF_ID"],
            location=location,
            project_id=project_id,
            dataset_prefix=dataset_prefix,
            data_dir=Path(os.getenv("LOCAL_DATA_DIR", "/tmp/data")),
            max_requests=max_requests,
        )
        return 0
    except Exception as exc:
        # Cloud Run logs only a safe error type; source URLs/body and credentials stay private.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
