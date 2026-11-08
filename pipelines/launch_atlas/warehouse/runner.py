"""One explicit acquisition → load → dbt → validation → retained-release chain."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from launch_atlas.config import Settings
from launch_atlas.pipeline import run_plan
from launch_atlas.storage.exports import export_current
from launch_atlas.storage.local import atomic_json, digest_file, read_json
from launch_atlas.storage.snapshots import _code_revision, _project_root, create_input_snapshot
from launch_atlas.storage.state import StateStore
from launch_atlas.warehouse.loading import (
    BigQueryLoader,
    CloudTarget,
    LoadPlan,
    execute_load_plan,
    prepare_load_plan,
)
from launch_atlas.warehouse.releases import (
    REQUIRED_REPORTING_TABLES,
    CandidateEvidence,
    CandidateRejected,
    ReleaseManifest,
    build_release_manifest,
    publish_candidate,
)
from launch_atlas.warehouse.reporting import BigQueryReportingStore


@dataclass(frozen=True)
class BuildResult:
    invocation_id: str
    manifest_sha256: str
    run_results_sha256: str
    test_count: int
    relations: dict[str, str]


class ModelBuilder(Protocol):
    def build(self, plan: LoadPlan, run_dir: Path, candidate_prefix: str) -> BuildResult: ...


def validate_dbt_artifacts(
    target_dir: Path, plan: LoadPlan, candidate_prefix: str, candidate_dataset: str
) -> BuildResult:
    """Require executed models/tests and bind their relation names to this invocation."""
    manifest_path, results_path = target_dir / "manifest.json", target_dir / "run_results.json"
    manifest, results = read_json(manifest_path), read_json(results_path)
    invocation = results.get("metadata", {}).get("invocation_id")
    if not invocation or manifest.get("metadata", {}).get("invocation_id") != invocation:
        raise CandidateRejected("dbt artifacts have inconsistent invocation IDs")
    variables = manifest.get("metadata", {}).get("project_name")
    if variables not in (None, "space_launch_atlas"):
        raise CandidateRejected("dbt artifacts belong to another project")
    nodes = {**manifest.get("nodes", {}), **manifest.get("unit_tests", {})}
    expected = {
        key
        for key, node in nodes.items()
        if node.get("resource_type") in {"model", "test", "unit_test"}
        and node.get("config", {}).get("enabled", True)
    }
    executed = {row["unique_id"]: row for row in results.get("results", [])}
    if not expected or not expected.issubset(executed):
        raise CandidateRejected("dbt build skipped one or more enabled models/tests")
    if any(row.get("status") not in {"success", "pass"} for row in executed.values()):
        raise CandidateRejected("dbt build contains failed, skipped or warning results")
    tests = [key for key in expected if nodes[key]["resource_type"] in {"test", "unit_test"}]
    if not tests:
        raise CandidateRejected("dbt build executed no tests")
    relations = {}
    for node in nodes.values():
        name = node.get("name")
        if node.get("resource_type") == "model" and name in REQUIRED_REPORTING_TABLES:
            if (
                node.get("database") != plan.target.project_id
                or node.get("schema") != candidate_dataset
                or node.get("alias") != candidate_prefix + name
            ):
                raise CandidateRejected("dbt reporting relation is outside this candidate")
            relations[name] = f"{node['database']}.{node['schema']}.{node['alias']}"
    if set(relations) != REQUIRED_REPORTING_TABLES:
        raise CandidateRejected("dbt manifest is missing reporting models")
    return BuildResult(
        str(invocation),
        digest_file(manifest_path),
        digest_file(results_path),
        len(tests),
        relations,
    )


class DbtBuilder:
    def __init__(
        self,
        candidate_dataset: str,
        maximum_bytes_billed: int = 100_000_000,
        project_dir: Path | None = None,
    ) -> None:
        self.candidate_dataset = candidate_dataset
        self.maximum_bytes_billed = maximum_bytes_billed
        self.project_dir = project_dir or _project_root() / "models"

    def build(self, plan: LoadPlan, run_dir: Path, candidate_prefix: str) -> BuildResult:
        pinned = read_json(plan.data_dir / "snapshots" / f"{plan.snapshot_id}.json")
        expected_code = pinned["transformations"]["code_revision"]["source_files_sha256"]
        if _code_revision(_project_root())["source_files_sha256"] != expected_code:
            raise CandidateRejected("Transformation code changed after input pinning")
        executable = shutil.which("dbt")
        if executable is None:
            raise ValueError("Install the locked warehouse dependency group to run dbt")
        profiles = run_dir / "profiles"
        profiles.mkdir(parents=True)
        shutil.copyfile(self.project_dir / "profiles.yml.example", profiles / "profiles.yml")
        environment = os.environ.copy()
        environment.update(
            {
                "GCP_PROJECT_ID": plan.target.project_id,
                "GCP_LOCATION": plan.target.location,
                "DBT_TARGET_DATASET": self.candidate_dataset,
                "DBT_SOURCE_DATASET": plan.target.source_dataset
                or plan.target.dataset_prefix + "_source",
                "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
                "DBT_MAXIMUM_BYTES_BILLED": str(self.maximum_bytes_billed),
                **{
                    f"DBT_{layer}_DATASET": self.candidate_dataset
                    for layer in ("STAGING", "CORE", "MARTS")
                },
            }
        )
        variables = {
            "input_snapshot_id": plan.snapshot_id,
            "source_table_prefix": plan.source_table_prefix,
            "candidate_prefix": candidate_prefix,
            "history_complete": plan.release_eligible,
            "source_check_as_of_utc": pinned["state"].get("latest_complete_refresh_as_of_utc"),
        }
        command = [
            executable,
            "build",
            "--project-dir",
            str(self.project_dir),
            "--profiles-dir",
            str(profiles),
            "--target-path",
            str(run_dir / "target"),
            "--log-path",
            str(run_dir / "logs"),
            "--vars",
            json.dumps(variables),
            "--no-partial-parse",
            "--no-version-check",
        ]
        completed = subprocess.run(
            command, env=environment, capture_output=True, text=True, timeout=3600, check=False
        )
        # dbt's detailed logs are already in its ignored logs directory.
        if completed.returncode != 0:
            raise CandidateRejected(f"dbt build failed; inspect {run_dir / 'logs' / 'dbt.log'}")
        if _code_revision(_project_root())["source_files_sha256"] != expected_code:
            raise CandidateRejected("Transformation code changed during the dbt build")
        return validate_dbt_artifacts(
            run_dir / "target", plan, candidate_prefix, self.candidate_dataset
        )


class WarehousePipeline:
    """Methods are the same dependency boundaries invoked by the Dagster job and tests."""

    def __init__(
        self,
        settings: Settings,
        target: CloudTarget,
        loader: BigQueryLoader,
        builder: ModelBuilder,
        reporting: BigQueryReportingStore,
        *,
        publish: bool = False,
        expected_active: str | None = None,
        refresh_plan_id: str | None = None,
    ) -> None:
        self.settings, self.target, self.loader = settings, target, loader
        self.builder, self.reporting = builder, reporting
        self.publish, self.expected_active = publish, expected_active
        self.refresh_plan_id = refresh_plan_id
        self.run_dir = settings.data_dir.resolve() / "warehouse" / "runs" / uuid4().hex
        self.run_dir.mkdir(parents=True)
        self.candidate_prefix = "sla_candidate_" + self.run_dir.name[:16] + "_"

    def acquire(self) -> dict[str, Any]:
        if self.refresh_plan_id:
            run_plan(self.settings, self.refresh_plan_id)
            if (
                StateStore(self.settings.data_dir).plan(self.refresh_plan_id)["status"]
                != "complete"
            ):
                raise CandidateRejected("Refresh is incomplete; downstream warehouse work stopped")
        if StateStore(self.settings.data_dir).summary()["launch_current"] == 0:
            raise CandidateRejected("No accepted launch data is available")
        return {"accepted_state": True}

    def pin(self, accepted: dict[str, Any]) -> LoadPlan:
        if not accepted.get("accepted_state"):
            raise CandidateRejected("Acquisition did not complete")
        export_current(self.settings)
        snapshot = create_input_snapshot(self.settings)
        return prepare_load_plan(self.settings.data_dir, snapshot["input_snapshot_id"], self.target)

    def load(self, plan: LoadPlan) -> dict[str, Any]:
        receipt = execute_load_plan(plan, self.loader)
        return {"plan": plan, "receipt": receipt}

    def build(self, loaded: dict[str, Any]) -> dict[str, Any]:
        plan = loaded["plan"]
        build = self.builder.build(plan, self.run_dir, self.candidate_prefix)
        counts, hashes = self.reporting.inspect_candidate(build.relations, plan.snapshot_id)
        snapshot = read_json(self.settings.data_dir / "snapshots" / f"{plan.snapshot_id}.json")
        as_of = snapshot["export"]["coverage"]["upcoming_as_of_utc"]
        evidence = CandidateEvidence(
            input_snapshot_id=plan.snapshot_id,
            load_receipt=loaded["receipt"],
            dbt_invocation_id=build.invocation_id,
            dbt_manifest_sha256=build.manifest_sha256,
            dbt_run_results_sha256=build.run_results_sha256,
            dbt_run_status="success",
            dbt_test_count=build.test_count,
            dbt_test_failures=0,
            candidate_validation_passed=True,
            candidate_validation_errors=(),
            reporting_row_counts=counts,
            analysis_as_of_utc=as_of,
            reporting_content_sha256=hashes,
        )
        from dataclasses import asdict

        atomic_json(self.run_dir / "candidate-evidence.json", asdict(evidence))
        return {"evidence": evidence, "relations": build.relations}

    def retain(self, candidate: dict[str, Any]) -> dict[str, Any]:
        evidence = candidate["evidence"]
        if not self.publish:
            return {
                "status": "candidate_built",
                "input_snapshot_id": evidence.input_snapshot_id,
                "release_eligible": evidence.load_receipt["release_eligible"],
                "reporting_row_counts": evidence.reporting_row_counts,
                "dbt_test_count": evidence.dbt_test_count,
            }
        # The eligibility gate runs before any reporting table/view is created or changed.
        manifest = build_release_manifest(evidence)
        self.reporting.retain_candidate(manifest, candidate["relations"])
        return {"manifest": manifest, "evidence": evidence}

    def activate(self, retained: dict[str, Any]) -> dict[str, Any]:
        if "manifest" in retained:
            manifest: ReleaseManifest = retained["manifest"]
            result: dict[str, Any] = publish_candidate(
                retained["evidence"], self.reporting, self.expected_active
            )
            atomic_json(self.run_dir / "release-manifest.json", manifest.to_dict())
        else:
            result = retained
        result["run_dir"] = str(self.run_dir)
        result["query_jobs"] = self.reporting.job_evidence
        atomic_json(self.run_dir / "result.json", result)
        return result
