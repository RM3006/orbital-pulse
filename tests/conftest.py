import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("live") or (
        request.node.get_closest_marker("integration")
        and os.environ.get("RUN_BIGQUERY_INTEGRATION") == "1"
    ):
        return

    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Default tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.fixture
def records() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parent / "fixtures/ll2_public_sample.json").read_text())[
        "results"
    ]
