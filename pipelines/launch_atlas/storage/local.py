import gzip
import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class LocalStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Serialize writers and the quota ledger for this data root; fail closed after a crash."""
        path = self.root / ".writer.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ValueError(
                "Data directory is locked; see docs/operations.md for recovery"
            ) from exc
        try:
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            yield
        finally:
            path.unlink(missing_ok=True)

    def run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
            raise ValueError("Invalid run ID")
        return self.root / "raw" / "ll2" / run_id

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        atomic_json(self.run_dir(manifest["run_id"]) / "manifest.json", manifest)

    def manifest(self, run_id: str) -> dict[str, Any]:
        return read_json(self.run_dir(run_id) / "manifest.json")

    def save_body(self, run_id: str, number: int, body: bytes) -> str:
        # Content suffix permits recovery if a process died between raw write and manifest commit.
        path = self.run_dir(run_id) / f"response-{number:04d}-{digest(body)[:16]}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if gzip.decompress(path.read_bytes()) != body:
                raise ValueError("Existing immutable raw artifact has conflicting content")
        else:
            temporary = path.with_suffix(".tmp")
            with temporary.open("wb") as handle:
                handle.write(gzip.compress(body, mtime=0))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        return path.relative_to(self.root).as_posix()

    def read_body(self, artifact: str, sha256: str) -> bytes:
        path = (self.root / artifact).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Artifact escapes the data directory")
        body = gzip.decompress(path.read_bytes())
        if digest(body) != sha256:
            raise ValueError("Raw artifact checksum mismatch")
        return body
