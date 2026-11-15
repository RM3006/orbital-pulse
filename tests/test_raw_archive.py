import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from launch_atlas.config import Settings
from launch_atlas.pipeline import import_raw, ingest
from launch_atlas.storage.local import LocalStore, digest
from launch_atlas.warehouse.raw_json import (
    RAW_SCHEMA,
    BigQueryRawResponseArchive,
    RawArchiveTarget,
    archive_local_run,
    local_raw_run_ids,
    make_raw_response_row,
)


def response_attempt(body: bytes) -> dict[str, Any]:
    return {
        "endpoint": "/2.3.0/launches/previous/",
        "query": {"include_suborbital": ["false"], "limit": ["100"]},
        "request_started_at_utc": "2026-11-11T10:00:00+00:00",
        "observed_at_utc": "2026-11-11T10:00:01+00:00",
        "http_status": 200,
        "source": "ll2",
        "api_version": "2.3.0",
        "content_type": "application/json; charset=utf-8",
        "sha256": digest(body),
    }


class CollectingArchive:
    def __init__(self) -> None:
        self.responses: list[tuple[str, int, str, dict[str, Any], bytes]] = []

    def archive_response(
        self,
        run_id: str,
        request_number: int,
        run_kind: str,
        attempt: dict[str, Any],
        body: bytes,
    ) -> str:
        self.responses.append((run_id, request_number, run_kind, attempt.copy(), body))
        return f"response-{request_number}"


def test_raw_response_row_preserves_exact_body_and_safe_metadata() -> None:
    body = b'{ "results": [{"name":"Voyager \xe2\x98\x84"}], "next": null }\n'
    attempt = response_attempt(body)

    first = make_raw_response_row("history-1957", 2, "live", attempt, body)
    second = make_raw_response_row("history-1957", 2, "live", attempt, body)

    assert first["response_id"] == second["response_id"]
    assert base64.b64decode(first["response_body"]) == body
    assert first["body_sha256"] == digest(body)
    assert first["endpoint"] == "/2.3.0/launches/previous/"
    assert "url" not in first
    assert json.loads(first["query_json"]) == attempt["query"]
    assert {field["name"] for field in RAW_SCHEMA} >= {
        "response_id",
        "request_started_at_utc",
        "response_body",
        "body_sha256",
    }


def test_raw_response_row_rejects_body_tampering_and_transport_failures() -> None:
    body = b'{"results":[]}'
    with pytest.raises(ValueError, match="checksum"):
        make_raw_response_row(
            "run-1", 1, "live", {**response_attempt(body), "sha256": "0" * 64}, body
        )
    with pytest.raises(ValueError, match="transport failures"):
        make_raw_response_row(
            "run-1", 1, "live", {**response_attempt(body), "http_status": None}, body
        )


def test_raw_response_row_preserves_missing_historical_request_time() -> None:
    body = b'{"count":0,"next":null,"results":[]}'
    attempt = response_attempt(body)
    attempt.pop("request_started_at_utc")

    row = make_raw_response_row("legacy-run", 1, "live", attempt, body)

    assert row["request_started_at_utc"] is None
    assert row["observed_at_utc"] == "2026-11-11T10:00:01+00:00"


def test_target_builds_source_table_and_rejects_missing_location() -> None:
    target = RawArchiveTarget("atlas-project", "space_launch_atlas", "EU")
    assert target.dataset_id == "space_launch_atlas_source"
    assert target.table_id == "atlas-project.space_launch_atlas_source.ll2_raw_response_pages"
    with pytest.raises(ValueError, match="location"):
        RawArchiveTarget("atlas-project", "space_launch_atlas", "")


def test_local_import_archive_replays_verified_page_without_network(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "ll2_public_sample.json"
    data_dir = tmp_path / "data"
    import_raw(
        Settings(data_dir=data_dir),
        fixture,
        "raw-archive-fixture",
        "2026-11-11T10:00:00Z",
    )
    archive = CollectingArchive()

    result = archive_local_run(data_dir, "raw-archive-fixture", archive)

    assert result["http_responses_archived"] == 1
    assert len(archive.responses) == 1
    run_id, request_number, run_kind, attempt, body = archive.responses[0]
    assert run_id == "raw-archive-fixture"
    assert request_number == 1
    assert run_kind == "offline_import"
    assert attempt["query"] == {"offline_import": ["true"]}
    assert body == fixture.read_bytes()


def test_all_runs_archiver_excludes_offline_imports_unless_explicit(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "ll2_public_sample.json"
    data_dir = tmp_path / "data"
    import_raw(
        Settings(data_dir=data_dir),
        fixture,
        "offline-run",
        "2026-11-11T10:00:00Z",
    )
    LocalStore(data_dir).save_manifest({"run_id": "live-run", "kind": "live"})

    assert local_raw_run_ids(data_dir) == ["live-run"]
    assert local_raw_run_ids(data_dir, include_offline_imports=True) == [
        "live-run",
        "offline-run",
    ]


def test_ingest_archives_body_before_rejecting_invalid_response(tmp_path: Path) -> None:
    raw_body = b'{"count":1,"next":null,"results":[broken]}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=raw_body,
            request=request,
        )

    archive = CollectingArchive()
    result = ingest(
        Settings(data_dir=tmp_path, page_size=1, max_requests=1),
        window="recent",
        start="2026-11-01T00:00:00Z",
        end="2026-11-02T00:00:00Z",
        run_id="archive-before-parse",
        transport=httpx.MockTransport(handler),
        raw_archive=archive,
    )

    assert result["status"] == "failed"
    assert len(archive.responses) == 1
    assert archive.responses[0][4] == raw_body
    assert archive.responses[0][3]["sha256"] == digest(raw_body)


def test_archive_failure_blocks_parse_but_keeps_local_raw_evidence(tmp_path: Path) -> None:
    raw_body = b'{"count":0,"next":null,"results":[]}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=raw_body,
            request=request,
        )

    class FailingArchive:
        def archive_response(
            self,
            run_id: str,
            request_number: int,
            run_kind: str,
            attempt: dict[str, Any],
            body: bytes,
        ) -> str:
            raise ValueError("simulated BigQuery raw archive failure")

    result = ingest(
        Settings(data_dir=tmp_path, page_size=1, max_requests=1),
        window="recent",
        start="2026-11-01T00:00:00Z",
        end="2026-11-02T00:00:00Z",
        run_id="archive-write-failed",
        transport=httpx.MockTransport(handler),
        raw_archive=FailingArchive(),
    )
    manifest = LocalStore(tmp_path).manifest("archive-write-failed")
    attempt = manifest["attempts"][0]

    assert result["status"] == "failed"
    assert attempt["sha256"] == digest(raw_body)
    assert LocalStore(tmp_path).read_body(attempt["artifact"], attempt["sha256"]) == raw_body


def test_bigquery_raw_archive_uses_schema_and_reuses_conflicted_job() -> None:
    pytest.importorskip("google.cloud.bigquery")
    from google.api_core.exceptions import Conflict

    target = RawArchiveTarget("atlas-project", "space_launch_atlas", "EU")
    body = b'{"count":0,"next":null,"results":[]}'

    class FakeJob:
        output_rows = 1
        error_result = None

        def result(self, timeout: int) -> None:
            assert timeout == 600

    class FakeClient:
        def __init__(self) -> None:
            self.row: dict[str, Any] | None = None
            self.job_id: str | None = None
            self.job_config: Any = None
            self.get_job_calls = 0

        def load_table_from_json(self, rows: list[dict[str, Any]], table_id: str, **kwargs: Any):
            assert table_id == target.table_id
            self.row = rows[0]
            self.job_id = kwargs["job_id"]
            self.job_config = kwargs["job_config"]
            assert kwargs["location"] == "EU"
            raise Conflict("same deterministic response job already exists")

        def get_job(self, job_id: str, **kwargs: Any) -> FakeJob:
            self.get_job_calls += 1
            assert job_id == self.job_id
            assert kwargs == {"project": "atlas-project", "location": "EU"}
            return FakeJob()

    client = FakeClient()
    archive = BigQueryRawResponseArchive(client, target)
    response_id = archive.archive_response("live-run", 1, "live", response_attempt(body), body)

    assert client.row is not None
    assert response_id == client.row["response_id"]
    assert client.job_id == "sla_raw_" + response_id
    assert client.get_job_calls == 1
    assert client.job_config.autodetect is False
    assert client.job_config.write_disposition == "WRITE_APPEND"
    assert client.job_config.create_disposition == "CREATE_NEVER"
    assert [field.name for field in client.job_config.schema] == [
        field["name"] for field in RAW_SCHEMA
    ]
