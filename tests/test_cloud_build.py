from __future__ import annotations

from scripts.cloud_build_worker import build_id_from_operation


def test_cloud_build_operation_exposes_build_id_in_metadata() -> None:
    assert (
        build_id_from_operation(
            {"metadata": {"build": {"id": "regional-build-id", "status": "QUEUED"}}}
        )
        == "regional-build-id"
    )


def test_cloud_build_operation_falls_back_to_response_resource_name() -> None:
    assert (
        build_id_from_operation(
            {"response": {"name": "projects/project/locations/europe-west1/builds/build-id"}}
        )
        == "build-id"
    )


def test_cloud_build_operation_without_build_id_is_not_guessed() -> None:
    assert build_id_from_operation({"name": "operations/build/project/op-id"}) is None
