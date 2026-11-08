"""Credential-free release gates and backend contracts.

The production BigQuery transaction is intentionally not implemented here. This
module validates candidate evidence before delegating atomic pointer changes to a
backend, which lets offline tests exercise publication ordering and failure safety.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from launch_atlas.warehouse.loading import SNAPSHOT_ID_PATTERN

RELEASE_ID_PATTERN = re.compile(r"^sla-release-v1-[a-f0-9]{24}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
REQUIRED_REPORTING_TABLES = frozenset(
    {
        "mart_launch_attempt_daily",
        "mart_family_outcomes_daily",
        "mart_stage_flight",
        "mart_upcoming_schedule",
        "mart_booster_flight",
        "mart_landing",
        "mart_site_cadence",
        "mart_schedule_movement",
        "mart_data_coverage",
        "core_dim_provider",
        "core_dim_vehicle_configuration",
        "core_dim_vehicle_family",
        "core_configuration_family",
        "core_dim_launch_site",
        "core_dim_pad",
    }
)


class CandidateRejected(ValueError):
    """Candidate evidence is incomplete or failed a release gate."""


class StalePublisher(RuntimeError):
    """The active release changed after the caller selected its expected value."""


@dataclass(frozen=True)
class CandidateEvidence:
    """Evidence collected by the loader, dbt invocation and candidate checks."""

    input_snapshot_id: str
    load_receipt: dict[str, Any]
    dbt_invocation_id: str
    dbt_manifest_sha256: str
    dbt_run_results_sha256: str
    dbt_run_status: str
    dbt_test_count: int
    dbt_test_failures: int
    candidate_validation_passed: bool
    candidate_validation_errors: tuple[str, ...]
    reporting_row_counts: dict[str, int]
    analysis_as_of_utc: str
    reporting_content_sha256: dict[str, str]


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    input_snapshot_id: str
    load_plan_id: str
    load_plan_sha256: str
    dbt_invocation_id: str
    dbt_manifest_sha256: str
    dbt_run_results_sha256: str
    analysis_as_of_utc: str
    reporting_row_counts: dict[str, int]
    reporting_content_sha256: dict[str, str]
    dbt_test_count: int
    validated: bool
    manifest_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReleaseBackend(Protocol):
    """Backend must commit metadata and pointer together using compare-and-swap."""

    def active_release_id(self) -> str | None: ...

    def publish_validated(
        self, manifest: ReleaseManifest, expected_previous_release_id: str | None
    ) -> bool:
        """Persist the validated release and select it atomically, or return False."""
        ...

    def retained_release(self, release_id: str) -> ReleaseManifest | None: ...

    def activate_retained(self, release_id: str, expected_current_release_id: str | None) -> bool:
        """Select an earlier retained release atomically, or return False."""
        ...


def _canonical_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_release_manifest(evidence: CandidateEvidence) -> ReleaseManifest:
    """Fail closed unless a nonempty, complete load and dbt candidate passed checks."""
    if not SNAPSHOT_ID_PATTERN.fullmatch(evidence.input_snapshot_id):
        raise CandidateRejected("Candidate input snapshot ID is invalid")
    receipt = evidence.load_receipt
    if receipt.get("status") != "ready":
        raise CandidateRejected("Candidate load receipt is not ready")
    if receipt.get("snapshot_id") != evidence.input_snapshot_id:
        raise CandidateRejected("Candidate load receipt references another input snapshot")
    if receipt.get("release_eligible") is not True:
        raise CandidateRejected("Input snapshot is not eligible for a complete reporting release")
    load_plan_id = receipt.get("plan_id")
    if not isinstance(load_plan_id, str) or not load_plan_id:
        raise CandidateRejected("Candidate load receipt has no plan ID")
    load_plan_sha256 = receipt.get("plan_sha256")
    if not isinstance(load_plan_sha256, str) or not SHA256_PATTERN.fullmatch(load_plan_sha256):
        raise CandidateRejected("Candidate load receipt has no valid plan checksum")
    if evidence.dbt_run_status != "success":
        raise CandidateRejected("dbt build did not finish successfully")
    if not evidence.dbt_invocation_id.strip():
        raise CandidateRejected("dbt invocation ID is missing")
    if not SHA256_PATTERN.fullmatch(evidence.dbt_manifest_sha256):
        raise CandidateRejected("dbt manifest checksum is missing or invalid")
    if not SHA256_PATTERN.fullmatch(evidence.dbt_run_results_sha256):
        raise CandidateRejected("dbt run-results checksum is missing or invalid")
    if evidence.dbt_test_count <= 0:
        raise CandidateRejected("Candidate has no executed dbt tests")
    if evidence.dbt_test_failures != 0:
        raise CandidateRejected("Candidate has failing dbt tests")
    if not evidence.candidate_validation_passed or evidence.candidate_validation_errors:
        raise CandidateRejected("Candidate-level validation did not pass")
    try:
        as_of = datetime.fromisoformat(evidence.analysis_as_of_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateRejected("Candidate analysis as-of timestamp is invalid") from exc
    if as_of.tzinfo is None:
        raise CandidateRejected("Candidate analysis as-of timestamp is missing")
    if REQUIRED_REPORTING_TABLES != set(evidence.reporting_row_counts):
        raise CandidateRejected("Candidate is missing one or more release 1 reporting tables")
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in evidence.reporting_row_counts.values()
    ):
        raise CandidateRejected("Reporting row counts must be nonnegative integers")
    if any(not name.strip() for name in evidence.reporting_row_counts):
        raise CandidateRejected("Reporting table names must not be empty")
    if evidence.reporting_row_counts["mart_launch_attempt_daily"] == 0:
        raise CandidateRejected("Candidate contains no launch-attempt reporting rows")
    if evidence.reporting_row_counts["mart_data_coverage"] != 1:
        raise CandidateRejected("Candidate must contain one snapshot coverage row")
    if set(evidence.reporting_content_sha256) != REQUIRED_REPORTING_TABLES or any(
        not SHA256_PATTERN.fullmatch(value) for value in evidence.reporting_content_sha256.values()
    ):
        raise CandidateRejected("Candidate reporting content checksums are missing or invalid")

    identity = {
        "contract_version": 1,
        "input_snapshot_id": evidence.input_snapshot_id,
        "load_plan_id": load_plan_id,
        "load_plan_sha256": load_plan_sha256,
        "dbt_invocation_id": evidence.dbt_invocation_id,
        "dbt_manifest_sha256": evidence.dbt_manifest_sha256,
        "dbt_run_results_sha256": evidence.dbt_run_results_sha256,
        "analysis_as_of_utc": evidence.analysis_as_of_utc,
        "reporting_row_counts": dict(sorted(evidence.reporting_row_counts.items())),
        "reporting_content_sha256": dict(sorted(evidence.reporting_content_sha256.items())),
        "dbt_test_count": evidence.dbt_test_count,
        "validated": True,
    }
    manifest_sha256 = _canonical_hash(identity)
    release_id = f"sla-release-v1-{manifest_sha256[:24]}"
    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise CandidateRejected("Generated release ID is invalid")
    return ReleaseManifest(
        release_id=release_id,
        input_snapshot_id=evidence.input_snapshot_id,
        load_plan_id=load_plan_id,
        load_plan_sha256=load_plan_sha256,
        dbt_invocation_id=evidence.dbt_invocation_id,
        dbt_manifest_sha256=evidence.dbt_manifest_sha256,
        dbt_run_results_sha256=evidence.dbt_run_results_sha256,
        analysis_as_of_utc=evidence.analysis_as_of_utc,
        reporting_row_counts=dict(sorted(evidence.reporting_row_counts.items())),
        reporting_content_sha256=dict(sorted(evidence.reporting_content_sha256.items())),
        dbt_test_count=evidence.dbt_test_count,
        validated=True,
        manifest_sha256=manifest_sha256,
    )


def publish_candidate(
    evidence: CandidateEvidence,
    backend: ReleaseBackend,
    expected_previous_release_id: str | None,
) -> dict[str, str]:
    """Validate first, then ask the backend for one atomic expected-value update."""
    manifest = build_release_manifest(evidence)
    active = backend.active_release_id()
    if active == manifest.release_id:
        if backend.retained_release(manifest.release_id) != manifest:
            raise CandidateRejected("Active release has no matching retained manifest/data")
        return {"status": "already_active", "release_id": manifest.release_id}
    if active != expected_previous_release_id:
        raise StalePublisher(
            f"Expected active release {expected_previous_release_id!r}; found {active!r}"
        )
    if not backend.publish_validated(manifest, expected_previous_release_id):
        raise StalePublisher("Active release changed during candidate publication")
    return {"status": "published", "release_id": manifest.release_id}


def validate_release_manifest(manifest: ReleaseManifest) -> None:
    """Reject corrupted metadata before publication or rollback uses it."""
    expected = build_release_manifest(
        CandidateEvidence(
            input_snapshot_id=manifest.input_snapshot_id,
            load_receipt={
                "status": "ready",
                "snapshot_id": manifest.input_snapshot_id,
                "plan_id": manifest.load_plan_id,
                "plan_sha256": manifest.load_plan_sha256,
                "release_eligible": True,
            },
            dbt_invocation_id=manifest.dbt_invocation_id,
            dbt_manifest_sha256=manifest.dbt_manifest_sha256,
            dbt_run_results_sha256=manifest.dbt_run_results_sha256,
            dbt_run_status="success",
            dbt_test_count=manifest.dbt_test_count,
            dbt_test_failures=0,
            candidate_validation_passed=True,
            candidate_validation_errors=(),
            reporting_row_counts=manifest.reporting_row_counts,
            analysis_as_of_utc=manifest.analysis_as_of_utc,
            reporting_content_sha256=manifest.reporting_content_sha256,
        )
    )
    if expected != manifest:
        raise CandidateRejected("Retained release manifest identity/checksum is invalid")


def rollback_release(
    release_id: str,
    backend: ReleaseBackend,
    expected_current_release_id: str,
) -> dict[str, str]:
    """Select a previously retained validated release with an expected-current check."""
    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise CandidateRejected("Rollback release ID is invalid")
    if backend.active_release_id() != expected_current_release_id:
        raise StalePublisher("Active release changed before rollback")
    retained = backend.retained_release(release_id)
    if retained is None or retained.validated is not True:
        raise CandidateRejected("Rollback target is not a retained validated release")
    if not backend.activate_retained(release_id, expected_current_release_id):
        raise StalePublisher("Active release changed during rollback")
    return {"status": "rolled_back", "release_id": release_id}
