"""Exact-response archiving for source HTTP responses in BigQuery.

The local raw archive remains the acquisition system of record until hosted
checkpoint/state persistence is implemented and verified. This module makes
BigQuery raw retention explicit and replay-safe without importing cloud SDKs
for local-only commands.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from launch_atlas.storage.local import LocalStore, digest, read_json

RAW_TABLE_NAME = "ll2_raw_response_pages"
RAW_SCHEMA: tuple[dict[str, str], ...] = (
    {"name": "response_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "run_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "run_kind", "type": "STRING", "mode": "REQUIRED"},
    {"name": "request_number", "type": "INT64", "mode": "REQUIRED"},
    {"name": "source", "type": "STRING", "mode": "REQUIRED"},
    {"name": "api_version", "type": "STRING", "mode": "REQUIRED"},
    {"name": "endpoint", "type": "STRING", "mode": "REQUIRED"},
    {"name": "query_json", "type": "STRING", "mode": "REQUIRED"},
    {"name": "request_started_at_utc", "type": "TIMESTAMP", "mode": "NULLABLE"},
    {"name": "observed_at_utc", "type": "TIMESTAMP", "mode": "REQUIRED"},
    {"name": "http_status", "type": "INT64", "mode": "REQUIRED"},
    {"name": "content_type", "type": "STRING", "mode": "NULLABLE"},
    {"name": "retry_after", "type": "STRING", "mode": "NULLABLE"},
    {"name": "body_sha256", "type": "STRING", "mode": "REQUIRED"},
    {"name": "response_body", "type": "BYTES", "mode": "REQUIRED"},
)


class RawResponseArchive(Protocol):
    def archive_response(
        self,
        run_id: str,
        request_number: int,
        run_kind: str,
        attempt: dict[str, Any],
        body: bytes,
    ) -> str: ...


class RawArchiveError(ValueError):
    """A response could not be durably archived before it was parsed."""


@dataclass(frozen=True)
class RawArchiveTarget:
    project_id: str
    dataset_prefix: str = "space_launch_atlas"
    location: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", self.project_id):
            raise ValueError("Invalid BigQuery project ID")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,100}", self.dataset_prefix):
            raise ValueError("Invalid BigQuery dataset prefix")
        if not self.location.strip():
            raise ValueError("BigQuery location is required")

    @property
    def dataset_id(self) -> str:
        return f"{self.dataset_prefix}_source"

    @property
    def table_id(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{RAW_TABLE_NAME}"


def make_raw_response_row(
    run_id: str,
    request_number: int,
    run_kind: str,
    attempt: dict[str, Any],
    body: bytes,
) -> dict[str, Any]:
    """Build a safe, byte-preserving BigQuery row from one HTTP response event."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise RawArchiveError("Invalid raw response run ID")
    if request_number < 1:
        raise RawArchiveError("Raw response request number must be positive")
    if run_kind not in {"live", "offline_import"}:
        raise RawArchiveError("Unsupported raw response run kind")
    endpoint = attempt.get("endpoint")
    if endpoint not in {
        "/2.3.0/launches/",
        "/2.3.0/launches/previous/",
        "/2.3.0/launches/upcoming/",
    }:
        raise RawArchiveError("Raw response endpoint is outside the verified LL2 API")
    query = attempt.get("query", {})
    if not isinstance(query, dict):
        raise RawArchiveError("Raw response query metadata must be an object")
    allowed_query_keys = {
        "limit",
        "offset",
        "mode",
        "ordering",
        "include_suborbital",
        "net__gte",
        "net__lt",
        "net__lte",
        "last_updated__gte",
        "last_updated__lte",
        "offline_import",
    }
    if set(query) - allowed_query_keys:
        raise RawArchiveError("Raw response query contains unapproved parameters")
    if any(
        not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str)
        for values in query.values()
    ):
        raise RawArchiveError("Raw response query values must be single strings")
    status = attempt.get("http_status")
    if not isinstance(status, int):
        raise RawArchiveError("Raw archive accepts HTTP responses, not transport failures")
    requested_at = attempt.get("request_started_at_utc")
    observed_at = attempt.get("observed_at_utc")
    if requested_at is not None and not isinstance(requested_at, str):
        raise RawArchiveError("Raw response request timestamp must be an ISO string or missing")
    if not isinstance(observed_at, str):
        raise RawArchiveError("Raw response observation timestamp is required")
    try:
        timestamps = [observed_at] + ([requested_at] if requested_at is not None else [])
        if any(
            datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None
            for value in timestamps
        ):
            raise ValueError
    except ValueError:
        raise RawArchiveError("Raw response timestamps must be timezone-aware ISO values") from None
    body_sha256 = digest(body)
    expected_hash = attempt.get("sha256")
    if expected_hash is not None and expected_hash != body_sha256:
        raise RawArchiveError("Local raw body checksum does not match its manifest")

    stable_fields = {
        "run_id": run_id,
        "request_number": request_number,
        "run_kind": run_kind,
        "source": str(attempt.get("source", "ll2")),
        "api_version": str(attempt.get("api_version", "2.3.0")),
        "endpoint": endpoint,
        "query_json": json.dumps(query, sort_keys=True, separators=(",", ":")),
        "request_started_at_utc": requested_at,
        "observed_at_utc": observed_at,
        "http_status": status,
        "content_type": attempt.get("content_type"),
        "retry_after": attempt.get("retry_after"),
        "body_sha256": body_sha256,
    }
    response_id = hashlib.sha256(
        json.dumps(stable_fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "response_id": response_id,
        "run_id": run_id,
        "run_kind": run_kind,
        "request_number": request_number,
        "source": stable_fields["source"],
        "api_version": stable_fields["api_version"],
        "endpoint": endpoint,
        "query_json": stable_fields["query_json"],
        "request_started_at_utc": requested_at,
        "observed_at_utc": observed_at,
        "http_status": status,
        "content_type": stable_fields["content_type"],
        "retry_after": stable_fields["retry_after"],
        "body_sha256": body_sha256,
        # BigQuery NDJSON represents BYTES as base64. The decoded value is the
        # exact response body, unlike a parsed/canonicalized JSON column.
        "response_body": base64.b64encode(body).decode("ascii"),
    }


class BigQueryRawResponseArchive:
    """Append one immutable raw HTTP response using an idempotent load job."""

    def __init__(self, client: Any, target: RawArchiveTarget):
        self._client = client
        self._target = target

    @classmethod
    def from_application_default_credentials(
        cls, target: RawArchiveTarget
    ) -> BigQueryRawResponseArchive:
        from google.cloud import bigquery

        return cls(bigquery.Client(project=target.project_id), target)

    def archive_response(
        self,
        run_id: str,
        request_number: int,
        run_kind: str,
        attempt: dict[str, Any],
        body: bytes,
    ) -> str:
        from google.api_core.exceptions import Conflict
        from google.cloud import bigquery

        row = make_raw_response_row(run_id, request_number, run_kind, attempt, body)
        job_id = "sla_raw_" + row["response_id"]
        schema = [
            bigquery.SchemaField(field["name"], field["type"], mode=field["mode"])
            for field in RAW_SCHEMA
        ]
        config = bigquery.LoadJobConfig(
            schema=schema,
            autodetect=False,
            create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        try:
            try:
                job = self._client.load_table_from_json(
                    [row],
                    self._target.table_id,
                    job_id=job_id,
                    location=self._target.location,
                    job_config=config,
                )
            except Conflict:
                job = self._client.get_job(
                    job_id, project=self._target.project_id, location=self._target.location
                )
            job.result(timeout=600)
            if job.error_result is not None or job.output_rows != 1:
                raise RawArchiveError("BigQuery raw-response load did not write exactly one row")
        except RawArchiveError:
            raise
        except Exception as exc:
            raise RawArchiveError(
                f"Could not archive LL2 raw response {row['response_id']} to BigQuery"
            ) from exc
        return row["response_id"]


def archive_local_run(data_dir: Path, run_id: str, archive: RawResponseArchive) -> dict[str, Any]:
    """Replay one local manifest's HTTP response evidence to the idempotent archive."""
    local = LocalStore(data_dir)
    manifest = local.manifest(run_id)
    attempts = manifest.get("attempts", [])
    if not attempts and manifest.get("pages"):
        # Older offline imports have no HTTP-attempt record. Preserve their
        # original response body, while making their curated provenance clear.
        attempts = [
            {
                **page,
                "endpoint": "/2.3.0/launches/previous/",
                "query": {"offline_import": ["true"]},
                "http_status": 200,
                "source": "ll2",
                "api_version": "2.3.0",
            }
            for page in manifest["pages"]
        ]
    archived: list[str] = []
    for index, attempt in enumerate(attempts, start=1):
        artifact = attempt.get("artifact")
        if not artifact:
            continue
        body = local.read_body(artifact, attempt["sha256"])
        archived.append(
            archive.archive_response(
                run_id,
                int(attempt.get("request_number", index)),
                str(manifest.get("kind", "live")),
                attempt,
                body,
            )
        )
    return {
        "run_id": run_id,
        "http_responses_archived": len(archived),
        "response_ids": archived,
    }


def local_raw_run_ids(data_dir: Path, *, include_offline_imports: bool = False) -> list[str]:
    root = data_dir / "raw" / "ll2"
    if not root.exists():
        return []
    run_ids = []
    for path in root.iterdir():
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = read_json(manifest_path)
        if include_offline_imports or manifest.get("kind") == "live":
            run_ids.append(path.name)
    return sorted(run_ids)
