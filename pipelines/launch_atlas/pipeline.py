import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import polars as pl

from launch_atlas.clients.ll2 import LL2Client, Page, PauseRun, SourceError, safe_url, validate_next
from launch_atlas.config import Settings
from launch_atlas.normalize import PARSER_VERSION, normalize
from launch_atlas.storage.local import LocalStore, atomic_json, digest


def build_url(settings: Settings, window: str, start: str | None, end: str | None) -> str:
    endpoint = {
        "recent": "launches/previous/",
        "history": "launches/previous/",
        "upcoming": "launches/upcoming/",
    }[window]
    query: dict[str, str | int] = {
        "limit": settings.page_size,
        "mode": "detailed",
        "include_suborbital": "false",
        "ordering": "-net" if window == "recent" else "net",
    }
    if start:
        query["net__gte"] = start
    if end:
        query["net__lt"] = end
    return settings.base_url + endpoint + "?" + urlencode(query)


def new_manifest(run_id: str, url: str, kind: str = "live") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "kind": kind,
        "source": "ll2",
        "api_version": "2.3.0",
        "parser_version": PARSER_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "initial_url": url,
        "next_url": url,
        "status": "created",
        "complete": False,
        "requests_total": 0,
        "attempts": [],
        "pages": [],
        "invocations": [],
    }


def replay(settings: Settings, run_id: str, *, store: LocalStore | None = None) -> dict[str, Any]:
    if store is None:
        owned = LocalStore(settings.data_dir)
        with owned.lock():
            return replay(settings, run_id, store=owned)
    manifest = store.manifest(run_id)
    inputs = []
    for entry in manifest["pages"]:
        page = Page.model_validate_json(store.read_body(entry["artifact"], entry["sha256"]))
        inputs.extend((item, entry) for item in page.results)
    tables, quality = normalize(inputs)
    output = store.root / "normalized" / run_id
    output.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        temporary = output / f"{name}.tmp.parquet"
        frame.write_parquet(temporary)
        temporary.replace(output / f"{name}.parquet")
    quality.update(
        {"run_id": run_id, "extraction_complete": manifest["complete"], "kind": manifest["kind"]}
    )
    atomic_json(output / "quality.json", quality)
    manifest["normalization"] = quality
    store.save_manifest(manifest)
    return quality


def ingest(
    settings: Settings,
    *,
    window: str = "recent",
    start: str | None = None,
    end: str | None = None,
    run_id: str | None = None,
    resume: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    store = LocalStore(settings.data_dir)
    with store.lock():
        if resume and not run_id:
            raise ValueError("Resume requires the exact --run-id")
        run_id = run_id or uuid4().hex
        directory = store.run_dir(run_id)
        if resume:
            manifest = store.manifest(run_id)
            if manifest["kind"] != "live":
                raise ValueError("Offline imports cannot be resumed against the API")
        else:
            if directory.exists():
                raise ValueError("Run already exists; use --resume or a new run ID")
            manifest = new_manifest(run_id, build_url(settings, window, start, end))
            store.save_manifest(manifest)
        if manifest["complete"]:
            quality = replay(settings, run_id, store=store)
            manifest = store.manifest(run_id)
            manifest["status"] = "complete" if quality["passed"] else "validation_failed"
            store.save_manifest(manifest)
            return manifest
        if manifest.get("retry_at", 0) and manifest["retry_at"] > time.time():
            return manifest
        client = LL2Client(settings, store, transport=transport)
        invocation: dict[str, Any] = {
            "started_at_utc": datetime.now(UTC).isoformat(),
            "budget": settings.max_requests,
        }
        manifest["invocations"].append(invocation)
        manifest["status"] = "running"
        manifest.pop("error", None)
        manifest.pop("retry_at", None)
        store.save_manifest(manifest)

        def record(response: httpx.Response | None, url: str, observed: str) -> None:
            manifest["requests_total"] += 1
            entry: dict[str, Any] = {
                "url": url,
                "endpoint": urlsplit(url).path,
                "query": parse_qs(urlsplit(url).query),
                "observed_at_utc": observed,
                "source": "ll2",
                "api_version": "2.3.0",
                "parser_version": PARSER_VERSION,
                "http_status": response.status_code if response is not None else None,
            }
            if response is not None:
                entry.update(
                    {
                        "artifact": store.save_body(
                            run_id, manifest["requests_total"], response.content
                        ),
                        "sha256": digest(response.content),
                        "content_type": response.headers.get("Content-Type"),
                        "retry_after": response.headers.get("Retry-After"),
                    }
                )
            manifest["attempts"].append(entry)
            store.save_manifest(manifest)

        try:
            seen = {entry["url"] for entry in manifest["pages"]}
            while manifest["next_url"]:
                url = safe_url(manifest["next_url"], settings.base_url)
                if url in seen:
                    raise SourceError("Pagination cycle detected")
                page = client.fetch(url, record)
                validate_next(page.next, manifest["initial_url"], settings.base_url)
                validate_next(page.next, url, settings.base_url)
                if page.next in seen or page.next == url:
                    raise SourceError("Pagination cycle detected")
                entry = dict(manifest["attempts"][-1])
                entry.update(
                    {"records": len(page.results), "source_count": page.count, "next": page.next}
                )
                manifest["pages"].append(entry)
                seen.add(url)
                manifest["next_url"] = page.next
                manifest["complete"] = page.next is None
                manifest["status"] = "complete" if manifest["complete"] else "running"
                store.save_manifest(manifest)
        except PauseRun as exc:
            manifest["status"] = str(exc)
            manifest["retry_at"] = exc.retry_at
        except (ValueError, OSError) as exc:
            manifest["status"] = "failed"
            manifest["error"] = str(exc)
        finally:
            invocation["requests"] = client.used
            invocation["ended_at_utc"] = datetime.now(UTC).isoformat()
            client.close()
            store.save_manifest(manifest)
        try:
            quality = replay(settings, run_id, store=store)
            manifest = store.manifest(run_id)
            if not quality["passed"]:
                manifest["status"] = "validation_failed"
        except (ValueError, OSError, pl.exceptions.PolarsError) as exc:
            manifest = store.manifest(run_id)
            manifest["status"] = "normalization_failed"
            manifest["error"] = str(exc)
        store.save_manifest(manifest)
        return manifest


def import_raw(settings: Settings, path: Path, run_id: str, observed_at: str) -> dict[str, Any]:
    """Import an explicitly orbital saved public page; never makes an API call."""
    store = LocalStore(settings.data_dir)
    body = path.read_bytes()
    page = Page.model_validate_json(body)
    # Validate input before creating durable state, including timezone-aware observation time.
    datetime_value = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if datetime_value.tzinfo is None:
        raise ValueError("Observation timestamp must contain a timezone")
    with store.lock():
        if store.run_dir(run_id).exists():
            raise ValueError("Run already exists; use replay")
        manifest = new_manifest(run_id, "offline_import", "offline_import")
        artifact = store.save_body(run_id, 1, body)
        manifest["pages"] = [
            {
                "artifact": artifact,
                "sha256": digest(body),
                "observed_at_utc": observed_at,
                "records": len(page.results),
                "source_count": page.count,
            }
        ]
        manifest.update({"next_url": None, "complete": page.next is None, "status": "imported"})
        store.save_manifest(manifest)
        quality = replay(settings, run_id, store=store)
        manifest = store.manifest(run_id)
        if not quality["passed"]:
            manifest["status"] = "validation_failed"
        store.save_manifest(manifest)
        return manifest


def summary(manifest: dict[str, Any]) -> str:
    return json.dumps(
        {
            "run_id": manifest["run_id"],
            "status": manifest["status"],
            "extraction_complete": manifest["complete"],
            "requests_total": manifest["requests_total"],
            "accepted_pages": len(manifest["pages"]),
            "records_fetched": sum(page["records"] for page in manifest["pages"]),
            "next_url": manifest["next_url"],
            "error": manifest.get("error"),
            "normalization": manifest.get("normalization"),
        },
        indent=2,
    )
