from __future__ import annotations

from dataclasses import replace

import pytest
from launch_atlas.warehouse.releases import (
    REQUIRED_REPORTING_TABLES,
    CandidateEvidence,
    CandidateRejected,
    ReleaseManifest,
    StalePublisher,
    build_release_manifest,
    publish_candidate,
    rollback_release,
    validate_release_manifest,
)

SNAPSHOT_ID = "sla-input-v1-" + "a" * 64
ROW_COUNTS = {
    **dict.fromkeys(REQUIRED_REPORTING_TABLES, 1),
    **{
        "mart_launch_attempt_daily": 12,
        "mart_family_outcomes_daily": 8,
        "mart_stage_flight": 10,
        "mart_upcoming_schedule": 4,
    },
}


def evidence(invocation_id: str = "dbt-run-a") -> CandidateEvidence:
    return CandidateEvidence(
        input_snapshot_id=SNAPSHOT_ID,
        load_receipt={
            "status": "ready",
            "snapshot_id": SNAPSHOT_ID,
            "plan_id": "sla-load-plan-a",
            "plan_sha256": "c" * 64,
            "release_eligible": True,
        },
        dbt_invocation_id=invocation_id,
        dbt_manifest_sha256="b" * 64,
        dbt_run_results_sha256="d" * 64,
        dbt_run_status="success",
        dbt_test_count=80,
        dbt_test_failures=0,
        candidate_validation_passed=True,
        candidate_validation_errors=(),
        reporting_row_counts=ROW_COUNTS,
        analysis_as_of_utc="2026-11-07T00:00:00Z",
        reporting_content_sha256=dict.fromkeys(REQUIRED_REPORTING_TABLES, "e" * 64),
    )


class MemoryReleaseBackend:
    """Test double for a transactional store; never contacts cloud services."""

    def __init__(self) -> None:
        self.active: str | None = None
        self.releases: dict[str, ReleaseManifest] = {}
        self.publish_calls = 0
        self.interrupt_publish_once = False
        self.reject_publish_once = False

    def active_release_id(self) -> str | None:
        return self.active

    def publish_validated(
        self, manifest: ReleaseManifest, expected_previous_release_id: str | None
    ) -> bool:
        self.publish_calls += 1
        if self.interrupt_publish_once:
            self.interrupt_publish_once = False
            raise RuntimeError("simulated interruption before transaction commit")
        if self.reject_publish_once:
            self.reject_publish_once = False
            return False
        if self.active != expected_previous_release_id:
            return False
        self.releases[manifest.release_id] = manifest
        self.active = manifest.release_id
        return True

    def retained_release(self, release_id: str) -> ReleaseManifest | None:
        return self.releases.get(release_id)

    def activate_retained(self, release_id: str, expected_current_release_id: str | None) -> bool:
        if self.active != expected_current_release_id or release_id not in self.releases:
            return False
        self.active = release_id
        return True


def test_failed_first_candidate_never_creates_active_release() -> None:
    backend = MemoryReleaseBackend()
    bad = replace(evidence(), dbt_test_failures=1)

    with pytest.raises(CandidateRejected, match="failing dbt tests"):
        publish_candidate(bad, backend, expected_previous_release_id=None)

    assert backend.active is None
    assert backend.releases == {}
    assert backend.publish_calls == 0


def test_failed_candidate_preserves_release_and_valid_release_can_be_rolled_back() -> None:
    backend = MemoryReleaseBackend()
    release_a = publish_candidate(evidence("dbt-a"), backend, None)["release_id"]
    bad_b = replace(evidence("dbt-b"), candidate_validation_passed=False)

    with pytest.raises(CandidateRejected, match="validation did not pass"):
        publish_candidate(bad_b, backend, expected_previous_release_id=release_a)
    assert backend.active == release_a

    release_c = publish_candidate(evidence("dbt-c"), backend, release_a)["release_id"]
    assert backend.active == release_c
    assert rollback_release(release_a, backend, release_c) == {
        "status": "rolled_back",
        "release_id": release_a,
    }
    assert backend.active == release_a
    assert release_c in backend.releases


def test_publication_retries_are_idempotent_and_stale_publishers_are_rejected() -> None:
    backend = MemoryReleaseBackend()
    first = evidence("dbt-a")
    release_a = publish_candidate(first, backend, None)["release_id"]

    assert publish_candidate(first, backend, None) == {
        "status": "already_active",
        "release_id": release_a,
    }
    with pytest.raises(StalePublisher, match="Expected active release"):
        publish_candidate(evidence("dbt-b"), backend, expected_previous_release_id=None)
    assert backend.active == release_a
    assert backend.publish_calls == 1


def test_interrupted_or_concurrent_publication_leaves_old_pointer_intact() -> None:
    backend = MemoryReleaseBackend()
    release_a = publish_candidate(evidence("dbt-a"), backend, None)["release_id"]
    candidate_b = evidence("dbt-b")
    backend.interrupt_publish_once = True

    with pytest.raises(RuntimeError, match="simulated interruption"):
        publish_candidate(candidate_b, backend, expected_previous_release_id=release_a)
    assert backend.active == release_a

    backend.reject_publish_once = True
    with pytest.raises(StalePublisher, match="changed during candidate publication"):
        publish_candidate(candidate_b, backend, expected_previous_release_id=release_a)
    assert backend.active == release_a

    release_b = publish_candidate(candidate_b, backend, expected_previous_release_id=release_a)[
        "release_id"
    ]
    assert backend.active == release_b


def test_candidate_requires_complete_load_nonempty_test_run_and_all_marts() -> None:
    backend = MemoryReleaseBackend()
    cases: list[tuple[CandidateEvidence, str]] = [
        (replace(evidence(), load_receipt={"status": "failed"}), "not ready"),
        (replace(evidence(), dbt_test_count=0), "no executed dbt tests"),
        (replace(evidence(), reporting_content_sha256={}), "content checksums"),
        (
            replace(evidence(), reporting_row_counts={"mart_launch_attempt_daily": 1}),
            "missing one or more",
        ),
        (
            replace(
                evidence(), load_receipt={**evidence().load_receipt, "release_eligible": False}
            ),
            "not eligible",
        ),
    ]
    for candidate, message in cases:
        with pytest.raises(CandidateRejected, match=message):
            publish_candidate(candidate, backend, expected_previous_release_id=None)
    assert backend.publish_calls == 0


def test_corrupted_retained_manifest_identity_is_rejected() -> None:
    manifest = build_release_manifest(evidence())
    validate_release_manifest(manifest)
    with pytest.raises(CandidateRejected, match="identity/checksum"):
        validate_release_manifest(replace(manifest, dbt_invocation_id="tampered"))
