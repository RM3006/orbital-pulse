"""Prepare and execute idempotent warehouse loads from a pinned local snapshot.

Importing this module never creates Google clients or discovers credentials. Cloud
clients are passed explicitly to ``execute_load_plan`` by the caller. Raw evidence
and prepared Parquet stay on the local filesystem; GCS is not used by this flow.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from launch_atlas.storage.local import LocalStore, atomic_json, digest_file, read_json

SNAPSHOT_ID_PATTERN = re.compile(r"^sla-input-v1-[a-f0-9]{64}$")
GENERATION_ID_PATTERN = re.compile(r"^revision-[0-9]{8}-[A-Za-z0-9]+$")
TABLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
LOAD_CONTRACT_VERSION = 2


def _canonical_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CloudTarget:
    """Non-secret warehouse destination values; placeholders are valid for local planning."""

    project_id: str = "placeholder-project"
    dataset_prefix: str = "space_launch_atlas"
    location: str = "replace-with-colocated-location"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", self.project_id):
            raise ValueError(
                "Project ID must be lowercase; use a real ID only for an authorized target"
            )
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", self.dataset_prefix):
            raise ValueError("Dataset prefix must start with a lowercase letter")
        if not self.location.strip() or self.location != self.location.strip():
            raise ValueError("Location must be explicit and contain no surrounding whitespace")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class LocalArtifact:
    """A raw or prepared file verified locally and never uploaded by this plan."""

    artifact_name: str
    local_path: Path
    sha256: str
    size_bytes: int
    content_type: str
    kind: str

    def to_dict(self, data_dir: Path) -> dict[str, Any]:
        return {
            "artifact_name": self.artifact_name,
            "local_path": self.local_path.relative_to(data_dir).as_posix(),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class TableLoad:
    table_name: str
    destination: str
    artifact_name: str
    sha256: str
    job_id: str
    row_count: int
    schema: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "destination": self.destination,
            "artifact_name": self.artifact_name,
            "sha256": self.sha256,
            "job_id": self.job_id,
            "row_count": self.row_count,
            "schema": list(self.schema),
        }


@dataclass(frozen=True)
class LoadPlan:
    snapshot_id: str
    plan_id: str
    source_table_prefix: str
    data_dir: Path
    plan_path: Path
    target: CloudTarget
    local_artifacts: tuple[LocalArtifact, ...]
    tables: tuple[TableLoad, ...]
    release_eligible: bool
    plan_sha256: str

    def summary(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "plan_id": self.plan_id,
            "source_table_prefix": self.source_table_prefix,
            "plan_path": self.plan_path.relative_to(self.data_dir).as_posix(),
            "local_artifacts": len(self.local_artifacts),
            "parquet_tables": len(self.tables),
            "rows": {table.table_name: table.row_count for table in self.tables},
            "release_eligible": self.release_eligible,
            "cloud_calls_made": False,
        }


@dataclass(frozen=True)
class TableReceipt:
    table_name: str
    destination: str
    job_id: str
    row_count: int


class BigQueryLoader(Protocol):
    def load_table(self, item: TableLoad, local_path: Path, location: str) -> TableReceipt: ...


def _parquet_rows(path: Path) -> int:
    try:
        return int(pl.scan_parquet(path).select(pl.len().alias("rows")).collect().item())
    except pl.exceptions.PolarsError as exc:
        raise ValueError(f"Prepared Parquet is unreadable: {path.name}") from exc


def _verify_snapshot(local: LocalStore, snapshot_id: str) -> dict[str, Any]:
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise ValueError("Invalid input snapshot ID")
    snapshot_path = local.root / "snapshots" / f"{snapshot_id}.json"
    if not snapshot_path.is_file():
        raise ValueError("Input snapshot file does not exist")
    document = read_json(snapshot_path)
    if document.get("input_snapshot_id") != snapshot_id:
        raise ValueError("Snapshot filename and input_snapshot_id disagree")
    if (
        document.get("contract_version") != 1
        or document.get("content_sha256") != snapshot_id.rsplit("-", 1)[-1]
    ):
        raise ValueError("Input snapshot contract version or content hash is invalid")
    export = document.get("export")
    if not isinstance(export, dict) or export.get("quality_passed") is not True:
        raise ValueError("Input snapshot does not reference a passing Parquet generation")
    generation_id = export.get("generation_id")
    if not isinstance(generation_id, str) or not GENERATION_ID_PATTERN.fullmatch(generation_id):
        raise ValueError("Input snapshot has an invalid export generation ID")
    identity = {
        key: value
        for key, value in document.items()
        if key not in {"input_snapshot_id", "content_sha256", "created_at_utc"}
    }
    identity["export"] = {
        key: export[key]
        for key in (
            "state_revision",
            "files",
            "rows",
            "coverage",
            "table_contracts",
            "quality_passed",
        )
    }
    if _canonical_hash(identity) != document["content_sha256"]:
        raise ValueError("Input snapshot content hash mismatch")

    generation_root = local.root / "exports" / "generations"
    generation = (generation_root / generation_id).resolve()
    if not generation.is_relative_to(generation_root.resolve()):
        raise ValueError("Snapshot generation path escapes the export directory")
    manifest_path = generation / "manifest.json"
    quality_path = generation / "quality.json"
    if (
        not manifest_path.is_file()
        or digest_file(manifest_path) != export.get("manifest_sha256")
        or not quality_path.is_file()
    ):
        raise ValueError("Pinned Parquet generation manifest/quality report is missing or changed")
    generation_manifest = read_json(manifest_path)
    quality = read_json(quality_path)
    if (
        generation_manifest.get("state_revision") != export.get("state_revision")
        or generation_manifest.get("files") != export.get("files")
        or quality.get("passed") is not True
        or quality.get("state_revision") != export.get("state_revision")
        or quality.get("rows") != export.get("rows")
    ):
        raise ValueError("Pinned Parquet generation does not match its snapshot metadata")
    files = export.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Snapshot declares no Parquet files")
    for filename, metadata in sorted(files.items()):
        if Path(filename).name != filename or not filename.endswith(".parquet"):
            raise ValueError("Snapshot declares an unsafe Parquet path")
        path = generation / filename
        if not path.is_file() or digest_file(path) != metadata.get("sha256"):
            raise ValueError(f"Pinned Parquet checksum mismatch: {filename}")
        if _parquet_rows(path) != metadata.get("rows"):
            raise ValueError(f"Pinned Parquet row count mismatch: {filename}")

    source_inputs = document.get("source_inputs")
    if not isinstance(source_inputs, list) or not source_inputs:
        raise ValueError("Input snapshot contains no verified source runs")
    for source in source_inputs:
        run_id = source.get("source_run_id")
        if not isinstance(run_id, str):
            raise ValueError("Input snapshot contains an invalid source run ID")
        manifest_path = local.run_dir(run_id) / "manifest.json"
        if not manifest_path.is_file() or digest_file(manifest_path) != source.get(
            "manifest_sha256"
        ):
            raise ValueError(f"Pinned source manifest mismatch: {run_id}")
        manifest = read_json(manifest_path)
        if (
            manifest.get("run_id") != run_id
            or manifest.get("complete") is not True
            or manifest.get("source") != "ll2"
        ):
            raise ValueError(f"Pinned source run is not complete LL2 data: {run_id}")
        pages = source.get("pages")
        manifest_pages = manifest.get("pages")
        if not isinstance(pages, list) or len(pages) != len(manifest_pages or []):
            raise ValueError(f"Pinned source page list mismatch: {run_id}")
        for page, saved_page in zip(pages, manifest_pages, strict=True):
            if page.get("artifact") != saved_page.get("artifact") or page.get(
                "sha256"
            ) != saved_page.get("sha256"):
                raise ValueError(f"Pinned source page identity mismatch: {run_id}")
            body = local.read_body(page["artifact"], page["sha256"])
            response = json.loads(body)
            if (
                len(response.get("results", [])) != page.get("records")
                or response.get("count") != page.get("source_count")
                or response.get("next") != page.get("next")
            ):
                raise ValueError(f"Pinned source page metadata mismatch: {run_id}")
    return document


def _bigquery_field(name: str, dtype: pl.DataType) -> dict[str, str]:
    if dtype == pl.String:
        return {"name": name, "type": "STRING", "mode": "NULLABLE"}
    if dtype == pl.Int64:
        return {"name": name, "type": "INT64", "mode": "NULLABLE"}
    if dtype == pl.Float64:
        return {"name": name, "type": "FLOAT64", "mode": "NULLABLE"}
    if dtype == pl.Boolean:
        return {"name": name, "type": "BOOL", "mode": "NULLABLE"}
    if dtype.base_type() == pl.Datetime:
        return {"name": name, "type": "TIMESTAMP", "mode": "NULLABLE"}
    if dtype == pl.List(pl.String):
        return {"name": name, "type": "STRING", "mode": "REPEATED"}
    raise ValueError(f"No explicit BigQuery schema mapping for {name}: {dtype}")


def _add_object(
    objects: dict[str, LocalArtifact],
    artifact_name: str,
    path: Path,
    content_type: str,
    kind: str,
) -> LocalArtifact:
    item = LocalArtifact(
        artifact_name, path, digest_file(path), path.stat().st_size, content_type, kind
    )
    prior = objects.get(artifact_name)
    if prior is not None and (prior.sha256 != item.sha256 or prior.size_bytes != item.size_bytes):
        raise ValueError(f"Conflicting local content for immutable artifact {artifact_name}")
    objects[artifact_name] = item
    return item


def prepare_load_plan(
    data_dir: Path,
    snapshot_id: str,
    target: CloudTarget | None = None,
) -> LoadPlan:
    """Verify a pinned snapshot and prepare local BigQuery inputs offline."""
    local = LocalStore(data_dir)
    target = target or CloudTarget()
    snapshot = _verify_snapshot(local, snapshot_id)
    source_table_prefix = f"sla_{snapshot_id.rsplit('-', 1)[-1][:24]}"
    generation = local.root / "exports" / "generations" / snapshot["export"]["generation_id"]
    prepared_tables = local.root / "warehouse" / "prepared" / snapshot_id / "tables"
    prepared_tables.mkdir(parents=True, exist_ok=True)
    objects: dict[str, LocalArtifact] = {}
    table_loads: list[TableLoad] = []

    for source in snapshot["source_inputs"]:
        run_id = source["source_run_id"]
        manifest_path = local.run_dir(run_id) / "manifest.json"
        _add_object(
            objects,
            f"snapshots/{snapshot_id}/runs/{run_id}/manifest.json",
            manifest_path,
            "application/json",
            "source_manifest",
        )
        for page in source["pages"]:
            artifact = page["artifact"]
            page_path = (local.root / artifact).resolve()
            if not page_path.is_relative_to(local.root):
                raise ValueError("Raw page artifact escapes the data directory")
            _add_object(
                objects,
                f"snapshots/{snapshot_id}/{artifact}",
                page_path,
                "application/gzip",
                "raw_response",
            )

    for filename, metadata in sorted(snapshot["export"]["files"].items()):
        table_name = Path(filename).stem
        if not TABLE_NAME_PATTERN.fullmatch(table_name):
            raise ValueError(f"Unsafe source table name: {table_name}")
        input_path = generation / filename
        frame = pl.read_parquet(input_path)
        if "input_snapshot_id" in frame.columns:
            raise ValueError("Source Parquet already contains the loader-managed snapshot column")
        frame = frame.with_columns(pl.lit(snapshot_id, dtype=pl.String).alias("input_snapshot_id"))
        prepared_path = prepared_tables / filename
        with tempfile.NamedTemporaryFile(
            dir=prepared_tables, suffix=".parquet.tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            frame.write_parquet(temporary_path, compression="zstd", statistics=True)
            temporary_path.replace(prepared_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        if _parquet_rows(prepared_path) != metadata.get("rows"):
            raise ValueError(f"Prepared table row count changed: {table_name}")
        item = _add_object(
            objects,
            f"snapshots/{snapshot_id}/tables/{filename}",
            prepared_path,
            "application/vnd.apache.parquet",
            "snapshot_table",
        )
        schema = tuple(_bigquery_field(name, dtype) for name, dtype in frame.schema.items())
        schema_version = _canonical_hash(schema)
        destination = (
            f"{target.project_id}.{target.dataset_prefix}_source.{source_table_prefix}_{table_name}"
        )
        job_hash = _canonical_hash(
            {
                "snapshot_id": snapshot_id,
                "table_name": table_name,
                "destination": destination,
                "object_sha256": item.sha256,
                "schema_sha256": schema_version,
            }
        )
        table_loads.append(
            TableLoad(
                table_name=table_name,
                destination=destination,
                artifact_name=item.artifact_name,
                sha256=item.sha256,
                job_id=f"sla_load_{job_hash}",
                row_count=frame.height,
                schema=schema,
            )
        )

    source_plans = snapshot["state"].get("plans", [])
    release_eligible = bool(source_plans) and all(
        plan.get("status") == "complete"
        and not any(
            name in ("planned", "split", "reconciliation_required", "conflict", "unstable")
            and count
            for name, count in plan.get("windows", {}).items()
        )
        for plan in source_plans
    )
    plan_identity = {
        "contract_version": LOAD_CONTRACT_VERSION,
        "snapshot_id": snapshot_id,
        "target": target.to_dict(),
        "source_table_prefix": source_table_prefix,
        "local_artifacts": [objects[name].to_dict(local.root) for name in sorted(objects)],
        "tables": [table.to_dict() for table in table_loads],
        "release_eligible": release_eligible,
    }
    plan_sha256 = _canonical_hash(plan_identity)
    plan_id = f"sla-load-v{LOAD_CONTRACT_VERSION}-{plan_sha256}"
    plan_root = local.root / "warehouse" / "plans" / plan_sha256[:16]
    plan_path = plan_root / "load-plan.json"
    plan_document = {
        "plan_id": plan_id,
        "plan_sha256": plan_sha256,
        "created_at_utc": snapshot.get("created_at_utc"),
        **plan_identity,
    }
    plan_root.mkdir(parents=True, exist_ok=True)
    if plan_path.exists():
        existing = read_json(plan_path)
        if existing.get("plan_id") != plan_id or existing.get("plan_sha256") != plan_sha256:
            raise ValueError("Existing load plan conflicts with its content-derived ID")
    else:
        atomic_json(plan_path, plan_document)
    return LoadPlan(
        snapshot_id=snapshot_id,
        plan_id=plan_id,
        source_table_prefix=source_table_prefix,
        data_dir=local.root,
        plan_path=plan_path,
        target=target,
        local_artifacts=tuple(objects[name] for name in sorted(objects)),
        tables=tuple(table_loads),
        release_eligible=release_eligible,
        plan_sha256=plan_sha256,
    )


def execute_load_plan(
    plan: LoadPlan,
    bigquery: BigQueryLoader,
) -> dict[str, Any]:
    """Verify all local inputs, load Parquet, and mark ready only after full success."""
    verified_artifacts: list[dict[str, Any]] = []
    artifacts_by_name = {item.artifact_name: item for item in plan.local_artifacts}
    for item in plan.local_artifacts:
        if not item.local_path.is_file() or digest_file(item.local_path) != item.sha256:
            raise ValueError(f"Local artifact changed after planning: {item.artifact_name}")
        verified_artifacts.append(
            {
                "artifact_name": item.artifact_name,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
        )

    table_receipts: list[TableReceipt] = []
    for table in plan.tables:
        artifact = artifacts_by_name.get(table.artifact_name)
        if artifact is None or artifact.kind != "snapshot_table":
            raise ValueError(f"Load plan has no local Parquet artifact: {table.table_name}")
        receipt = bigquery.load_table(table, artifact.local_path, plan.target.location)
        if (
            receipt.table_name != table.table_name
            or receipt.destination != table.destination
            or receipt.job_id != table.job_id
            or receipt.row_count != table.row_count
        ):
            raise ValueError(f"BigQuery load result failed its contract: {table.table_name}")
        table_receipts.append(receipt)

    result = {
        "status": "ready",
        "snapshot_id": plan.snapshot_id,
        "plan_id": plan.plan_id,
        "plan_sha256": plan.plan_sha256,
        "release_eligible": plan.release_eligible,
        "local_artifacts_verified": verified_artifacts,
        "table_receipts": [asdict(receipt) for receipt in table_receipts],
    }
    receipt_path = plan.plan_path.with_name("load-receipt.json")
    atomic_json(receipt_path, result)
    return {**result, "receipt_path": receipt_path.relative_to(plan.data_dir).as_posix()}


class GoogleBigQuerySourceLoader:
    """Local-file BigQuery Parquet loader with deterministic, retry-safe job IDs."""

    def __init__(self, client: Any, project_id: str):
        self._client = client
        self._project_id = project_id

    @classmethod
    def from_application_default_credentials(cls, project_id: str):
        from google.cloud import bigquery

        return cls(bigquery.Client(project=project_id), project_id)

    def load_table(self, item: TableLoad, local_path: Path, location: str) -> TableReceipt:
        from google.api_core.exceptions import Conflict
        from google.cloud import bigquery

        schema = [
            bigquery.SchemaField(field["name"], field["type"], mode=field["mode"])
            for field in item.schema
        ]
        config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            schema=schema,
            autodetect=False,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        try:
            with local_path.open("rb") as parquet_file:
                job = self._client.load_table_from_file(
                    parquet_file,
                    item.destination,
                    rewind=True,
                    size=local_path.stat().st_size,
                    job_id=item.job_id,
                    location=location,
                    job_config=config,
                )
        except Conflict:
            job = self._client.get_job(item.job_id, project=self._project_id, location=location)
        job.result(timeout=1800)
        if job.error_result is not None or job.output_rows != item.row_count:
            raise ValueError(f"BigQuery load row count/error mismatch: {item.table_name}")
        return TableReceipt(item.table_name, item.destination, item.job_id, int(job.output_rows))
