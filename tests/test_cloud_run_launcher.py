import pytest

from scripts.run_cloud_worker_once import cloud_run_overrides


def test_cloud_run_override_can_cap_one_live_request() -> None:
    request = cloud_run_overrides("proof-id", 1)

    container = request["overrides"]["containerOverrides"][0]
    assert container["env"] == [
        {"name": "CHECKPOINT_PROOF_ID", "value": "proof-id"},
        {"name": "WORKER_MAX_REQUESTS", "value": "1"},
    ]


@pytest.mark.parametrize("limit", [0, 9])
def test_cloud_run_override_rejects_unbounded_request_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="1-8 requests"):
        cloud_run_overrides("proof-id", limit)
