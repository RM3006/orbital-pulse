"""Versioned, checksum-verified contracts for local warehouse input snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from launch_atlas.config import Settings
from launch_atlas.normalize import PARSER_VERSION
from launch_atlas.observations import HASH_VERSION, OBSERVATION_KEY_VERSION
from launch_atlas.storage.local import LocalStore, atomic_json, digest, digest_file, read_json
from launch_atlas.storage.state import SCHEMA_VERSION, StateStore

SNAPSHOT_CONTRACT_VERSION = 1


def _canonical_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _project_root() -> Path:
    package_file = Path(__file__).resolve()
    for parent in package_file.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return package_file.parents[1]


def _code_revision(root: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for fixed in ("pyproject.toml", "uv.lock", "docs/metrics.md"):
        path = root / fixed
        if path.is_file():
            files[fixed] = digest(path.read_bytes())
    for directory in ("pipelines/launch_atlas", "models"):
        source = root / directory
        if not source.is_dir():
            continue
        for path in sorted(source.rglob("*")):
            if path.is_file() and path.suffix.lower() in {
                ".py",
                ".sql",
                ".yml",
                ".yaml",
                ".jinja",
                ".md",
            }:
                files[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    if not files:
        raise ValueError("Could not identify the project transformation source files")
    commit: str | None = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
            timeout=3,
        )
        commit = result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    project_version = None
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as handle:
            project_version = tomllib.load(handle).get("project", {}).get("version")
    return {
        "repository_commit": commit,
        "project_version": project_version,
        "source_files_sha256": _canonical_hash(files),
        "source_file_count": len(files),
    }


def _parquet_rows(path: Path) -> int:
    try:
        return int(pl.scan_parquet(path).select(pl.len().alias("rows")).collect().item())
    except pl.exceptions.PolarsError as exc:
        raise ValueError(f"Exported Parquet is unreadable: {path.name}") from exc


def _validated_generation(local: LocalStore, state: StateStore) -> dict[str, Any]:
    exports = local.root / "exports"
    active_path = exports / "active.json"
    if not active_path.is_file():
        raise ValueError("Create a validated state export before creating an input snapshot")
    active = read_json(active_path)
    generation_id = active.get("generation_id")
    if not isinstance(generation_id, str) or not re.fullmatch(
        r"revision-[0-9]{8}-[A-Za-z0-9]+", generation_id
    ):
        raise ValueError("Active export has an invalid generation ID")
    generation = (exports / "generations" / generation_id).resolve()
    if not generation.is_relative_to((exports / "generations").resolve()):
        raise ValueError("Active export path escapes its generation directory")
    manifest_path = generation / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Active export generation is missing its manifest")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest != active:
        raise ValueError("Active pointer and generation manifest disagree")
    quality_path = generation / "quality.json"
    if not quality_path.is_file():
        raise ValueError("Active export generation is missing its quality report")
    quality = read_json(quality_path)
    if active.get("quality_passed") is not True or quality.get("passed") is not True:
        raise ValueError("Active export does not have a passing quality report")
    revision = state.revision()
    if active.get("state_revision") != revision or quality.get("state_revision") != revision:
        raise ValueError("Active export is stale relative to the accepted SQLite state")
    files = active.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Active export does not declare its Parquet files")
    verified_files = {}
    for filename, metadata in sorted(files.items()):
        if Path(filename).name != filename or not filename.endswith(".parquet"):
            raise ValueError("Export manifest contains an unsafe file path")
        path = generation / filename
        if not path.is_file():
            raise ValueError(f"Exported table is missing: {filename}")
        file_hash = digest_file(path)
        if file_hash != metadata.get("sha256"):
            raise ValueError(f"Export checksum or row count mismatch: {filename}")
        rows = _parquet_rows(path)
        if rows != metadata.get("rows"):
            raise ValueError(f"Export checksum or row count mismatch: {filename}")
        verified_files[filename] = {"sha256": file_hash, "rows": rows}
    if quality.get("rows") != active.get("rows"):
        raise ValueError("Export row counts differ between quality report and manifest")
    return {
        "generation_id": generation_id,
        "state_revision": revision,
        "manifest_sha256": digest(manifest_bytes),
        "files": verified_files,
        "rows": active["rows"],
        "coverage": active.get("coverage", {}),
        "table_contracts": active.get("table_contracts", {}),
        "quality_passed": True,
    }


def _source_inputs(local: LocalStore, state: StateStore) -> list[dict[str, Any]]:
    inputs = []
    for acquisition in state.acquisition_records():
        run_id = acquisition["source_run_id"]
        run_directory = local.run_dir(run_id)
        manifest_path = run_directory / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"Accepted acquisition {run_id} has no raw manifest")
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        if (
            manifest.get("run_id") != run_id
            or manifest.get("source") != "ll2"
            or manifest.get("complete") is not True
            or manifest.get("kind") not in ("live", "offline_import")
            or manifest.get("status") not in ("complete", "imported")
        ):
            raise ValueError(f"Accepted acquisition {run_id} does not reference a complete LL2 run")
        pages = []
        for page in manifest.get("pages", []):
            artifact = page.get("artifact")
            page_hash = page.get("sha256")
            if not artifact or not page_hash:
                raise ValueError(f"Accepted acquisition {run_id} has incomplete page provenance")
            body = local.read_body(artifact, page_hash)
            response = json.loads(body)
            results = response.get("results")
            if not isinstance(results, list) or len(results) != page.get("records"):
                raise ValueError(f"Raw page record count mismatch for acquisition {run_id}")
            if response.get("count") != page.get("source_count"):
                raise ValueError(f"Raw page source count mismatch for acquisition {run_id}")
            if response.get("next") != page.get("next"):
                raise ValueError(f"Raw page pagination mismatch for acquisition {run_id}")
            pages.append(
                {
                    "endpoint": page.get("endpoint"),
                    "query": page.get("query", {}),
                    "artifact": artifact,
                    "sha256": page_hash,
                    "records": len(results),
                    "source_count": response["count"],
                    "next": response.get("next"),
                }
            )
        if not pages:
            raise ValueError(f"Accepted acquisition {run_id} has no raw pages")
        window = {
            key: acquisition.get(key)
            for key in (
                "window_id",
                "plan_id",
                "plan_type",
                "stream_id",
                "start_utc",
                "end_utc",
                "pass_number",
                "extraction_status",
                "reconciled",
                "frozen_as_of_utc",
                "query",
            )
        }
        inputs.append(
            {
                "acquisition_id": acquisition["acquisition_id"],
                "source_run_id": run_id,
                "observed_at_utc": acquisition["observed_at_utc"],
                "kind": manifest["kind"],
                "source": manifest["source"],
                "api_version": manifest.get("api_version"),
                "parser_version": manifest.get("parser_version"),
                "manifest_sha256": digest(manifest_bytes),
                "page_count": len(pages),
                "pages": pages,
                "window": window,
            }
        )
    return inputs


def create_input_snapshot(settings: Settings) -> dict[str, Any]:
    """Pin current accepted state, validated Parquet and checksummed raw provenance."""
    local = LocalStore(settings.data_dir)
    with local.lock():
        state = StateStore(settings.data_dir)
        generation = _validated_generation(local, state)
        source_inputs = _source_inputs(local, state)
        if not source_inputs:
            raise ValueError("Cannot create an input snapshot without accepted source runs")
        as_of = state.latest_complete_refresh_asof()
        project_root = _project_root()
        contract = {
            "contract_version": SNAPSHOT_CONTRACT_VERSION,
            "source": "ll2",
            "state": {
                "schema_version": SCHEMA_VERSION,
                "revision": state.revision(),
                "row_counts": state.summary(),
                "coverage_cursors": state.coverage_cursors(),
                "plans": state.plan_summary(),
                "latest_complete_refresh_as_of_utc": as_of.isoformat() if as_of else None,
            },
            "export": generation,
            "transformations": {
                "parser_version": PARSER_VERSION,
                "semantic_hash_version": HASH_VERSION,
                "observation_event_key_version": OBSERVATION_KEY_VERSION,
                "code_revision": _code_revision(project_root),
            },
            "source_inputs": source_inputs,
        }
        export_identity = {
            key: generation[key]
            for key in (
                "state_revision",
                "files",
                "rows",
                "coverage",
                "table_contracts",
                "quality_passed",
            )
        }
        identity = {
            **contract,
            "export": export_identity,
        }
        content_hash = _canonical_hash(identity)
        snapshot_id = f"sla-input-v{SNAPSHOT_CONTRACT_VERSION}-{content_hash}"
        document = {
            "input_snapshot_id": snapshot_id,
            "content_sha256": content_hash,
            "created_at_utc": datetime.now(UTC).isoformat(),
            **contract,
        }
        snapshot_directory = local.root / "snapshots"
        snapshot_path = snapshot_directory / f"{snapshot_id}.json"
        if snapshot_path.exists():
            existing = read_json(snapshot_path)
            if (
                existing.get("input_snapshot_id") != snapshot_id
                or existing.get("content_sha256") != content_hash
            ):
                raise ValueError("Existing input snapshot conflicts with its content-derived ID")
            return {**existing, "snapshot_path": snapshot_path.relative_to(local.root).as_posix()}
        atomic_json(snapshot_path, document)
        return {**document, "snapshot_path": snapshot_path.relative_to(local.root).as_posix()}
