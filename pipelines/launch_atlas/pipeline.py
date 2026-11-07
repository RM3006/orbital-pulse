import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import polars as pl

from launch_atlas.clients.ll2 import (
    LL2Client,
    Page,
    PauseRun,
    RequestBudget,
    SourceError,
    safe_url,
    validate_next,
)
from launch_atlas.config import Settings
from launch_atlas.normalize import PARSER_VERSION, normalize
from launch_atlas.storage.local import LocalStore, atomic_json, digest
from launch_atlas.storage.state import StateStore
from launch_atlas.windows import split_history_year


def build_url(
    settings: Settings,
    window: str,
    start: str | None,
    end: str | None,
    *,
    include_suborbital: bool = False,
    last_updated: bool = False,
) -> str:
    endpoint = {
        "recent": "launches/previous/",
        "history": "launches/previous/",
        "corrections": "launches/previous/",
        "upcoming": "launches/upcoming/",
    }[window]
    query: dict[str, str | int] = {
        "limit": settings.page_size,
        "mode": "detailed",
        "include_suborbital": str(include_suborbital).lower(),
        "ordering": "-last_updated" if last_updated else "-net" if window == "recent" else "net",
    }
    if start:
        query["last_updated__gte" if last_updated else "net__gte"] = start
    if end:
        query["last_updated__lte" if last_updated else "net__lt"] = end
    return settings.base_url + endpoint + "?" + urlencode(query)


def new_manifest(run_id: str, url: str, kind: str = "live") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "kind": kind,
        "source": "ll2",
        "api_version": "2.3.0",
        "manifest_version": 2,
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


def merge_run_state(
    settings: Settings, run_id: str, *, window_id: str | None = None
) -> dict[str, Any]:
    """Explicitly apply one complete, validated, replayable run to cross-run state."""
    local = LocalStore(settings.data_dir)
    with local.lock():
        manifest = local.manifest(run_id)
        if not manifest.get("complete"):
            raise ValueError("Only a complete raw run can be merged into persistent state")
        quality = replay(settings, run_id, store=local)
        if not quality["passed"]:
            raise ValueError("Run quality checks must pass before persistent state merge")
        inputs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for page_entry in manifest["pages"]:
            page = Page.model_validate_json(
                local.read_body(page_entry["artifact"], page_entry["sha256"])
            )
            inputs.extend((record, page_entry) for record in page.results)
        state = StateStore(settings.data_dir)
        if window_id is None:
            return state.apply_acquisition(
                run_id, run_id, inputs, request_evidence=manifest.get("attempts", [])
            )
        window = state.window(window_id)
        if window["extraction_status"] == "extracted":
            source_count = max(
                (int(page.get("source_count", 0)) for page in manifest["pages"]), default=0
            )
            unique_count = len({str(record["id"]) for record, _ in inputs})
            state.validate_window(window_id, quality["passed"], source_count, unique_count)
            window = state.window(window_id)
        if window["extraction_status"] != "validated":
            if window["extraction_status"] == "committed":
                return {"acquisition_id": run_id, "already_applied": True}
            raise ValueError("Extraction window must be complete and validated before merge")
        if not inputs:
            latest_receipt = max(page["observed_at_utc"] for page in manifest["pages"])
            state.commit_empty_window(
                window_id,
                acquisition_id=run_id,
                source_run_id=run_id,
                observed_at_utc=latest_receipt,
            )
            return {"acquisition_id": run_id, "already_applied": False, "empty": True}
        return state.apply_acquisition(
            run_id,
            run_id,
            inputs,
            window_id=window_id,
            request_evidence=manifest.get("attempts", []),
        )


def run_plan(
    settings: Settings,
    plan_id: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Run pending frozen windows with one invocation-wide request budget."""
    local = LocalStore(settings.data_dir)
    state = StateStore(settings.data_dir)
    plan = state.plan(plan_id)
    config = plan["config"]
    if int(config.get("page_size", 0)) < 1:
        raise ValueError("Frozen plan has no valid page size")
    plan_settings = settings.model_copy(update={"page_size": int(config["page_size"])})
    budget = RequestBudget(settings.max_requests)
    results = []
    window_rows = state.plan_windows(plan_id)
    priority = {"recent": 0, "upcoming": 1, "corrections": 2}
    window_rows.sort(
        key=lambda row: (
            priority.get(row["stream_id"], -1),
            row["start_utc"],
            row["pass_number"],
            row["window_id"],
        )
    )
    for window_row in window_rows:
        if budget.used >= budget.limit:
            break
        if state.window(window_row["window_id"])["extraction_status"] in ("committed", "split"):
            continue
        if window_row["extraction_status"] == "committed":
            continue
        query = json.loads(window_row["query_json"])
        run_id = window_row["window_id"]
        exists = local.run_dir(run_id).exists()
        manifest = local.manifest(run_id) if exists else None
        manifest = ingest(
            plan_settings,
            window=query["window"],
            start=query["start"],
            end=query["end"],
            run_id=run_id,
            resume=exists,
            transport=transport,
            budget=budget,
            include_suborbital=bool(query.get("include_suborbital", False)),
            last_updated=bool(query.get("last_updated", False)),
        )
        source_count = max(
            (int(page.get("source_count", 0)) for page in manifest["pages"]), default=0
        )
        threshold = int(config.get("dense_source_count_split_threshold", 250))
        if (
            query["window"] == "history"
            and window_row["start_utc"].endswith("01-01T00:00:00+00:00")
            and source_count > threshold
        ):
            count = split_history_year(state, settings, window_row)
            results.append(
                {
                    "window_id": run_id,
                    "status": "split_into_months",
                    "source_count": source_count,
                    "child_windows": count,
                }
            )
            continue
        if manifest["complete"]:
            if window_row["extraction_status"] in ("planned", "extracting"):
                state.checkpoint_window(run_id, None)
                state.mark_window_extracted(run_id)
            result = merge_run_state(settings, run_id, window_id=run_id)
            results.append({"window_id": run_id, "status": "committed", **result})
        else:
            if window_row["extraction_status"] in ("planned", "extracting"):
                state.checkpoint_window(run_id, manifest.get("next_url"))
            results.append(
                {
                    "window_id": run_id,
                    "status": manifest["status"],
                    "next_url": manifest.get("next_url"),
                }
            )
            if budget.used >= budget.limit or manifest["status"] not in (
                "budget_stopped",
                "throttled",
                "quota_stopped",
                "retry_wait",
            ):
                break
            break
    final_windows = state.plan_windows(plan_id)
    pending = sum(row["extraction_status"] not in ("committed", "split") for row in final_windows)
    unreconciled = (
        sum(
            row["pass_number"] > 1
            and row["extraction_status"] == "committed"
            and not row["reconciled"]
            for row in final_windows
        )
        if plan["plan_type"] in ("backfill", "reconcile")
        else 0
    )
    complete = pending == 0 and unreconciled == 0
    state.set_plan_status(
        plan_id,
        "complete"
        if complete
        else "reconciliation_required"
        if pending == 0 and unreconciled
        else "in_progress"
        if any(row["extraction_status"] == "committed" for row in final_windows)
        else "planned",
    )
    if complete:
        completed_plan = state.plan(plan_id)
        if completed_plan["plan_type"] == "refresh":
            frozen_as_of = completed_plan["frozen_as_of_utc"]
            state.set_latest_refresh_asof(frozen_as_of)
            for window_row in final_windows:
                if window_row["extraction_status"] != "committed":
                    continue
                run_id = window_row["window_id"]
                manifest_path = local.run_dir(run_id) / "manifest.json"
                if not manifest_path.exists():
                    continue
                completed_manifest = local.manifest(run_id)
                completed_manifest.update(
                    {
                        "manifest_version": 3,
                        "plan_id": plan_id,
                        "plan_type": completed_plan["plan_type"],
                        "plan_status": "complete",
                        "plan_frozen_as_of_utc": frozen_as_of,
                        "window_id": run_id,
                        "window_stream_id": window_row["stream_id"],
                    }
                )
                local.save_manifest(completed_manifest)
    return {
        "plan_id": plan_id,
        "requests_this_invocation": budget.used,
        "request_budget": budget.limit,
        "windows_committed_this_invocation": sum(row["status"] == "committed" for row in results),
        "windows_pending": pending,
        "windows_unreconciled": unreconciled,
        "results": results,
        "complete": complete,
    }


def rebuild_state(
    settings: Settings, output_dir: Path, *, include_run_ids: set[str] | None = None
) -> dict[str, Any]:
    """Rebuild disposable state offline; demo imports require explicit run IDs."""
    from launch_atlas.storage.exports import export_current

    source = LocalStore(settings.data_dir)
    target_root = output_dir.resolve()
    if target_root == source.root:
        raise ValueError("Rebuild output must be a separate directory")
    if target_root.exists() and any(target_root.iterdir()):
        raise ValueError("Rebuild output directory must be empty; accepted state is never replaced")
    target_root.mkdir(parents=True, exist_ok=True)
    target = Settings.model_validate({**settings.model_dump(), "data_dir": target_root})
    state = StateStore(target_root)
    source_runs = source.root / "raw" / "ll2"
    manifests = []
    for manifest_path in source_runs.glob("*/manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        accepted_kind = manifest.get("kind") == "live" or (
            manifest.get("kind") == "offline_import"
            and manifest.get("run_id") in (include_run_ids or set())
        )
        if manifest.get("source") == "ll2" and manifest.get("complete") is True and accepted_kind:
            manifests.append(manifest)
    manifests.sort(key=lambda item: (item.get("created_at_utc", ""), item["run_id"]))
    refresh_context = [
        manifest["plan_frozen_as_of_utc"]
        for manifest in manifests
        if manifest.get("plan_type") == "refresh"
        and manifest.get("plan_status") == "complete"
        and manifest.get("plan_frozen_as_of_utc")
    ]
    if refresh_context:
        state.set_latest_refresh_asof(max(refresh_context))
    applied = []
    for manifest in manifests:
        run_id = manifest["run_id"]
        inputs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for page_entry in manifest.get("pages", []):
            page = Page.model_validate_json(
                source.read_body(page_entry["artifact"], page_entry["sha256"])
            )
            inputs.extend((record, page_entry) for record in page.results)
        if not inputs:
            # A completed empty source run has evidence but cannot change launch state.
            continue
        _, quality = normalize(inputs)
        if not quality["passed"]:
            raise ValueError(f"Raw run {run_id} fails current parser/quality checks")
        source_run_dir = source.run_dir(run_id)
        target_run_dir = LocalStore(target_root).run_dir(run_id)
        target_run_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_run_dir, target_run_dir)
        result = state.apply_acquisition(
            run_id,
            run_id,
            inputs,
            request_evidence=manifest.get("attempts", []),
        )
        applied.append(result)
    if not applied:
        raise ValueError("No complete validated LL2 raw runs were found for rebuild")
    generation = export_current(target)
    return {
        "output_dir": str(target_root),
        "runs_applied": len(applied),
        "state_revision": state.revision(),
        "state": state.summary(),
        "generation": generation,
    }


def ingest(
    settings: Settings,
    *,
    window: str = "recent",
    start: str | None = None,
    end: str | None = None,
    run_id: str | None = None,
    resume: bool = False,
    transport: httpx.BaseTransport | None = None,
    budget: RequestBudget | None = None,
    include_suborbital: bool = False,
    last_updated: bool = False,
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
            manifest = new_manifest(
                run_id,
                build_url(
                    settings,
                    window,
                    start,
                    end,
                    include_suborbital=include_suborbital,
                    last_updated=last_updated,
                ),
            )
            store.save_manifest(manifest)
        if manifest["complete"]:
            quality = replay(settings, run_id, store=store)
            manifest = store.manifest(run_id)
            manifest["status"] = "complete" if quality["passed"] else "validation_failed"
            store.save_manifest(manifest)
            return manifest
        if manifest.get("retry_at", 0) and manifest["retry_at"] > time.time():
            return manifest
        shared_budget = budget or RequestBudget(settings.max_requests)
        request_start = shared_budget.used
        client = LL2Client(settings, store, transport=transport, budget=shared_budget)
        invocation: dict[str, Any] = {
            "started_at_utc": datetime.now(UTC).isoformat(),
            "budget": shared_budget.limit,
        }
        manifest["invocations"].append(invocation)
        manifest["status"] = "running"
        manifest.pop("error", None)
        manifest.pop("retry_at", None)
        store.save_manifest(manifest)

        def record(
            response: httpx.Response | None, url: str, observed: str, requested: str
        ) -> None:
            manifest["requests_total"] += 1
            entry: dict[str, Any] = {
                "url": url,
                "endpoint": urlsplit(url).path,
                "query": parse_qs(urlsplit(url).query),
                "observed_at_utc": observed,
                "request_started_at_utc": requested,
                "source": "ll2",
                "api_version": "2.3.0",
                "parser_version": PARSER_VERSION,
                "http_status": response.status_code if response is not None else None,
                "transport_error": "transport_error" if response is None else None,
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
            invocation["requests"] = client.used - request_start
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
