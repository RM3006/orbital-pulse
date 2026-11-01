import gzip
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import polars as pl
import pytest
from launch_atlas.clients.ll2 import LL2Client, PauseRun, SourceError, retry_seconds, safe_url
from launch_atlas.config import Settings
from launch_atlas.pipeline import build_url, import_raw, ingest, replay
from launch_atlas.storage.local import LocalStore, digest


def test_budget_resume_idempotency_and_replay(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings = Settings(data_dir=tmp_path, max_requests=1, page_size=1)
    initial = build_url(settings, "history", "1957-01-01T00:00:00Z", "1958-01-01T00:00:00Z")
    following = initial + "&offset=1"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["Accept"] == "application/json"
        assert "SpaceLaunchAtlas" in request.headers["User-Agent"]
        offset = int(parse_qs(request.url.query.decode()).get("offset", [0])[0])
        return httpx.Response(
            200,
            json={
                "count": 2,
                "next": following if offset == 0 else None,
                "results": [records[offset]],
            },
        )

    transport = httpx.MockTransport(handler)
    first = ingest(
        settings,
        window="history",
        start="1957-01-01T00:00:00Z",
        end="1958-01-01T00:00:00Z",
        run_id="resume",
        transport=transport,
    )
    assert first["status"] == "budget_stopped"
    assert first["complete"] is False
    assert first["next_url"] == following
    assert first["requests_total"] == 1
    second = ingest(settings, run_id="resume", resume=True, transport=transport)
    assert second["status"] == "complete"
    assert second["requests_total"] == 2
    assert second["next_url"] is None
    assert len(calls) == 2
    assert second["normalization"]["rows"]["fact_launch_attempt"] == 2
    output = tmp_path / "normalized/resume"
    before = {p.name: digest(p.read_bytes()) for p in output.glob("*.parquet")}
    replay(settings, "resume")
    ingest(settings, run_id="resume", resume=True, transport=transport)
    assert len(calls) == 2  # completed resume is a network-free no-op
    assert before == {p.name: digest(p.read_bytes()) for p in output.glob("*.parquet")}
    assert pl.read_parquet(output / "fact_launch_attempt.parquet").height == 2


def test_failed_page_does_not_advance_checkpoint(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings = Settings(data_dir=tmp_path, max_requests=2)
    url = build_url(settings, "recent", None, None) + "&offset=5"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"count": 20, "next": url, "results": records[:2]})
        return httpx.Response(
            200, text="<html>upstream unavailable</html>", headers={"Content-Type": "text/html"}
        )

    result = ingest(settings, run_id="bad-page", transport=httpx.MockTransport(handler))
    assert result["status"] == "failed"
    assert result["next_url"] == url
    assert not result["complete"]
    assert len(result["pages"]) == 1
    assert len(result["attempts"]) == 2
    error_body = LocalStore(tmp_path).read_body(
        result["attempts"][1]["artifact"], result["attempts"][1]["sha256"]
    )
    assert b"<html>" in error_body


@pytest.mark.parametrize(
    "target",
    [
        "https://attacker.invalid/2.3.0/launches/",
        "http://ll.thespacedevs.com/2.3.0/launches/",
        "https://ll.thespacedevs.com/2.2.0/launches/",
        "https://user:secret@ll.thespacedevs.com/2.3.0/launches/",
        "https://ll.thespacedevs.com/2.3.0/launches/?token=secret",
    ],
)
def test_unsafe_urls_rejected(target: str) -> None:
    with pytest.raises(SourceError):
        safe_url(target, Settings().base_url)


def test_pagination_cannot_escape_or_change_scope(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    for index, target in enumerate(
        [
            "https://attacker.invalid/2.3.0/launches/",
            build_url(Settings(), "recent", None, None).replace(
                "include_suborbital=false", "include_suborbital=true"
            )
            + "&offset=5",
        ]
    ):
        calls = []

        def handler(
            request: httpx.Request,
            calls: list[httpx.Request] = calls,
            target: str = target,
        ) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"count": 10, "next": target, "results": records[:1]})

        result = ingest(
            Settings(data_dir=tmp_path / str(index)),
            run_id="unsafe",
            transport=httpx.MockTransport(handler),
        )
        assert result["status"] == "failed"
        assert len(calls) == 1
        assert not result["complete"]
        assert result["pages"] == []


def test_throttle_is_persisted_and_resume_does_not_hammer(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, json={"detail": "throttled"}, headers={"Retry-After": "3600"})

    settings = Settings(data_dir=tmp_path)
    result = ingest(settings, run_id="throttle", transport=httpx.MockTransport(handler))
    assert result["status"] == "throttled"
    assert result["requests_total"] == 1
    assert result["retry_at"] is not None
    again = ingest(settings, run_id="throttle", resume=True, transport=httpx.MockTransport(handler))
    assert again["status"] == "throttled"
    assert len(calls) == 1


def test_transient_retry_counts_against_budget(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    store = LocalStore(tmp_path)
    calls, saved, sleeps = [], [], []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "temporary"}, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"count": 1, "next": None, "results": records[:1]})

    client = LL2Client(
        Settings(data_dir=tmp_path, max_requests=2),
        store,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    try:
        result = client.fetch(
            build_url(Settings(), "recent", None, None), lambda *args: saved.append(args)
        )
        assert len(result.results) == 1
        assert client.used == 2
        assert len(saved) == 2
        assert sleeps == [2]
        with pytest.raises(PauseRun, match="budget_stopped"):
            client.fetch(build_url(Settings(), "recent", None, None), lambda *args: None)
        assert len(calls) == 2
    finally:
        client.close()


def test_shared_local_quota_across_clients(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    first = LL2Client(Settings(data_dir=tmp_path, max_requests=8), store, clock=lambda: 10000)
    second = LL2Client(Settings(data_dir=tmp_path, max_requests=8), store, clock=lambda: 10000)
    try:
        for _ in range(8):
            first.reserve()
        for _ in range(7):
            second.reserve()
        with pytest.raises(PauseRun, match="quota_stopped"):
            second.reserve()
    finally:
        first.close()
        second.close()


def test_checksum_and_path_guards(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    artifact = store.save_body("integrity", 1, b'{"source": "original"}')
    (tmp_path / artifact).write_bytes(gzip.compress(b'{"source": "tampered"}'))
    with pytest.raises(ValueError, match="checksum"):
        store.read_body(artifact, digest(b'{"source": "original"}'))
    with pytest.raises(ValueError, match="escapes"):
        store.read_body("../outside.json.gz", "")
    with pytest.raises(ValueError, match="Invalid run ID"):
        store.run_dir("../outside")


def test_import_and_replay_fixture(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    fixture = Path(__file__).parent / "fixtures/ll2_public_sample.json"
    result = import_raw(settings, fixture, "offline", "2026-11-01T16:52:44Z")
    assert result["requests_total"] == 0
    assert result["normalization"]["rows"]["launch_record_current"] == 6
    assert replay(settings, "offline")["passed"]


def test_lock_is_exclusive(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    with store.lock():
        with pytest.raises(ValueError, match="locked"):
            with store.lock():
                pass
    assert not (tmp_path / ".writer.lock").exists()


def test_retry_after_http_date() -> None:
    now = datetime(2026, 11, 1, 16, 59, tzinfo=UTC).timestamp()
    assert retry_seconds("Sun, 01 Nov 2026 17:00:00 GMT", now) == 60
    assert retry_seconds("not a date", 0) == 60


@pytest.mark.parametrize("body", [b"not json", b'{"count": 5, "next": null, "results": []}'])
def test_bad_envelopes_fail_closed(tmp_path: Path, body: bytes) -> None:
    result = ingest(
        Settings(data_dir=tmp_path),
        run_id="bad-envelope",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=body, headers={"Content-Type": "application/json"}
            )
        ),
    )
    assert result["status"] == "failed"
    assert not result["complete"]


def test_schema_failure_keeps_raw(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    broken = dict(records[0], id="invalid UUID")
    result = ingest(
        Settings(data_dir=tmp_path),
        run_id="schema",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"count": 1, "next": None, "results": [broken]}
            )
        ),
    )
    assert result["status"] == "normalization_failed"
    assert len(result["pages"]) == 1
    assert (tmp_path / result["pages"][0]["artifact"]).exists()


def test_long_retry_after_blocks_early_resume(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"error": "unavailable"}, headers={"Retry-After": "3600"})

    settings = Settings(data_dir=tmp_path)
    first = ingest(settings, run_id="wait", transport=httpx.MockTransport(handler))
    assert first["status"] == "retry_wait"
    second = ingest(settings, run_id="wait", resume=True, transport=httpx.MockTransport(handler))
    assert second["status"] == "retry_wait"
    assert len(calls) == 1


def test_raw_write_can_recover_before_manifest_commit(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    original = b'{"unknown_future_field": 42}'
    first = store.save_body("recover", 1, original)
    assert store.save_body("recover", 1, original) == first
    second = store.save_body("recover", 1, b'{"unknown_future_field": 43}')
    assert first != second
    assert store.read_body(first, digest(original)) == original


def test_transport_error_stays_within_budget(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("synthetic offline transport failure")

    result = ingest(
        Settings(data_dir=tmp_path, max_requests=1),
        run_id="transport",
        transport=httpx.MockTransport(handler),
    )
    assert result["status"] == "budget_stopped"
    assert result["requests_total"] == 1
    assert result["attempts"][0]["http_status"] is None
    assert not result["complete"]
    assert len(calls) == 1
