"""BigQuery scratch prototype for restoring a quiescent LL2 worker."""

from __future__ import annotations

import base64
import io
import re
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from threading import Lock
from uuid import uuid4

from google.api_core.exceptions import Conflict
from google.cloud import bigquery

from launch_atlas.storage.local import LocalStore, digest, read_json
from launch_atlas.worker import worker_lock

CHUNK = 2 * 1024**2
MAX_BUNDLE = 256 * 1024**2
MAX_RESTORED = 768 * 1024**2
MAX_QUERY = 64 * 1024**2


def checkpoint(root: Path) -> bytes:
    """Capture replay inputs and progress without copying a live SQLite WAL."""
    db = "state/launch_atlas.sqlite"
    out = io.BytesIO()
    with (
        worker_lock(root / "worker"),
        LocalStore(root).lock(),
        tempfile.TemporaryDirectory() as tmp,
    ):
        copy = Path(tmp) / "state.sqlite"
        with (
            closing(sqlite3.connect((root / db).resolve().as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(copy)) as dst,
        ):
            src.backup(dst)
        files = {db: copy, "worker/state.json": root / "worker/state.json"}
        for relative in ("quota.json", "worker/stop.json", "exports/active.json"):
            if (root / relative).is_file():
                files[relative] = root / relative
        roots = [root / "raw" / "ll2", root / "snapshots"]
        if "exports/active.json" in files:
            generation = read_json(files["exports/active.json"])["generation_id"]
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", generation):
                raise ValueError("Invalid active export generation")
            roots.append(root / "exports" / "generations" / generation)
        for folder in roots:
            for path in folder.rglob("*"):
                if path.is_file() and not path.name.endswith(".tmp"):
                    files[path.relative_to(root).as_posix()] = path
        if sum(path.stat().st_size for path in files.values()) > MAX_RESTORED:
            raise ValueError("Checkpoint exceeds restore size limit")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for name, path in sorted(files.items()):
                archive.write(path, name)
    bundle = out.getvalue()
    if len(bundle) > MAX_BUNDLE:
        raise ValueError("Checkpoint exceeds upload size limit")
    return bundle


def restore(bundle: bytes, expected_sha: str, root: Path) -> None:
    """Restore verified files into an empty directory on a replacement executor."""
    if len(bundle) > MAX_BUNDLE or digest(bundle) != expected_sha:
        raise ValueError("Checkpoint size or SHA-256 does not match")
    if root.exists() and any(root.iterdir()):
        raise ValueError("Restore requires an empty working directory")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        members = archive.infolist()
        names = {item.filename for item in members}
        if (
            len(names) != len(members)
            or sum(item.file_size for item in members) > MAX_RESTORED
            or not {"worker/state.json", "state/launch_atlas.sqlite"} <= names
        ):
            raise ValueError("Invalid checkpoint contents")
        for name in names:
            path = PurePosixPath(name)
            if (
                not re.fullmatch(r"[a-zA-Z0-9_.\-/]+", name)
                or path.as_posix() != name
                or path.is_absolute()
                or ".." in path.parts
            ):
                raise ValueError("Unsafe checkpoint path")
        archive.extractall(root)


class BigQueryCheckpointProof:
    """Scratch only: one control row, and one row per (generation digest, chunk index).

    The owner deliberately has no expiry. A crash before commit leaves the run
    blocked because the previous executor may have made an unrecorded LL2 call.
    """

    def __init__(self, client: bigquery.Client, location: str, proof_id: str | None = None) -> None:
        self.client, self.location = client, location
        self.proof_id = proof_id or uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", self.proof_id):
            raise ValueError("Invalid worker proof ID")
        prefix = f"{client.project}.space_launch_atlas_scratch.worker_proof_{self.proof_id}"
        self.control, self.payload = prefix + "_control", prefix + "_payload"
        self.billed_bytes = 0
        self._meter_lock = Lock()

    @contextmanager
    def scratch(self) -> Iterator[BigQueryCheckpointProof]:
        """Use expiring tables and remove them on exit."""
        self.create_scratch_tables()
        try:
            yield self
        finally:
            self.delete_scratch_tables()

    def create_scratch_tables(self) -> None:
        """Create short-lived tables for an explicit integration or one-off proof."""
        schema = {
            self.control: [
                ("key", "STRING"),
                ("generation", "STRING"),
                ("owner", "STRING"),
                ("active_slot", "INT64"),
                ("completed_slot", "INT64"),
            ],
            self.payload: [("generation", "STRING"), ("chunk_index", "INT64"), ("body", "BYTES")],
        }
        created = []
        try:
            for table_id, columns in schema.items():
                table = bigquery.Table(
                    table_id, schema=[bigquery.SchemaField(name, kind) for name, kind in columns]
                )
                table.expires = datetime.now(UTC) + timedelta(hours=2)
                self.client.create_table(table, timeout=20)
                created.append(table_id)
        except Exception:
            for table_id in reversed(created):
                self.client.delete_table(table_id, not_found_ok=True, timeout=20)
            raise

    def delete_scratch_tables(self) -> None:
        """Delete only this proof's scratch tables after its execution is terminal."""
        for table_id in (self.payload, self.control):
            self.client.delete_table(table_id, not_found_ok=True, timeout=20)

    def _load(self, table: str, rows: list[dict[str, object]], job_id: str) -> None:
        cfg = bigquery.LoadJobConfig(
            create_disposition="CREATE_NEVER", write_disposition="WRITE_APPEND"
        )
        try:
            job = self.client.load_table_from_json(
                rows, table, job_id=job_id, location=self.location, job_config=cfg
            )
        except Conflict:
            existing = self.client.get_job(job_id, location=self.location)
            if not isinstance(existing, bigquery.LoadJob):
                raise ValueError("Checkpoint job ID is not a load job") from None
            job = existing
        job.result(timeout=90)
        if job.output_rows != len(rows):
            raise ValueError("Checkpoint load row count does not match")

    def _save(self, bundle: bytes) -> str:
        if not 0 < len(bundle) <= MAX_BUNDLE:
            raise ValueError("Invalid checkpoint bundle size")
        generation = digest(bundle)
        rows: list[dict[str, object]] = [
            {
                "generation": generation,
                "chunk_index": start // CHUNK,
                "body": base64.b64encode(bundle[start : start + CHUNK]).decode("ascii"),
            }
            for start in range(0, len(bundle), CHUNK)
        ]
        self._load(self.payload, rows, self.payload.rsplit(".", 1)[1] + "_" + generation)
        return generation

    def seed(self, bundle: bytes) -> None:
        generation = self._save(bundle)
        self._load(
            self.control,
            [{"key": "worker", "generation": generation, "completed_slot": -1}],
            self.control.rsplit(".", 1)[1] + "_seed",
        )

    def _update(self, statement: str, owner: str, slot: int, generation: str = "") -> bool:
        config = bigquery.QueryJobConfig(
            maximum_bytes_billed=MAX_QUERY,
            query_parameters=[
                bigquery.ScalarQueryParameter("owner", "STRING", owner),
                bigquery.ScalarQueryParameter("slot", "INT64", slot),
                bigquery.ScalarQueryParameter("generation", "STRING", generation),
            ],
        )
        job = self.client.query(
            f"UPDATE `{self.control}` {statement}", location=self.location, job_config=config
        )
        job.result(timeout=90)
        with self._meter_lock:
            self.billed_bytes += job.total_bytes_billed or 0
        if job.num_dml_affected_rows not in (0, 1):
            raise ValueError("Checkpoint control row is not unique")
        return job.num_dml_affected_rows == 1

    def claim(self, owner: str, slot: int) -> str | None:
        if not self._update(
            "SET owner=@owner, active_slot=@slot "
            "WHERE key='worker' AND owner IS NULL AND completed_slot < @slot",
            owner,
            slot,
        ):
            return None
        rows = list(self.client.list_rows(self.control, max_results=2, timeout=20))
        if len(rows) != 1 or rows[0]["owner"] != owner or rows[0]["active_slot"] != slot:
            raise ValueError("Worker cannot verify checkpoint ownership")
        return str(rows[0]["generation"])

    def read(self, generation: str) -> bytes:
        parts = sorted(
            (int(row["chunk_index"]), bytes(row["body"]))
            for row in self.client.list_rows(self.payload, timeout=20)
            if row["generation"] == generation
        )
        if not parts or [i for i, _ in parts] != list(range(len(parts))):
            raise ValueError("Missing or duplicate checkpoint chunks")
        body = b"".join(chunk for _, chunk in parts)
        if len(body) > MAX_BUNDLE or digest(body) != generation:
            raise ValueError("BigQuery checkpoint failed integrity validation")
        return body

    def commit(self, owner: str, slot: int, bundle: bytes, *, completed: bool) -> bool:
        generation = self._save(bundle)
        cursor = ", completed_slot=@slot" if completed else ""
        return self._update(
            f"SET generation=@generation, owner=NULL, active_slot=NULL{cursor} "
            "WHERE key='worker' AND owner=@owner AND active_slot=@slot",
            owner,
            slot,
            generation,
        )
