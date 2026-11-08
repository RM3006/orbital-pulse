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

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def allowed_loopback(address: Any) -> bool:
        return bool(request.node.get_closest_marker("local_asyncio")) and (
            isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")
        )

    def blocked(sock: Any, address: Any, *args: Any, **kwargs: Any) -> Any:
        if allowed_loopback(address):
            return original_connect(sock, address, *args, **kwargs)
        raise AssertionError("Default tests must not use the network")

    def blocked_ex(sock: Any, address: Any, *args: Any, **kwargs: Any) -> Any:
        if allowed_loopback(address):
            return original_connect_ex(sock, address, *args, **kwargs)
        raise AssertionError("Default tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_ex)


@pytest.fixture
def records() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parent / "fixtures/ll2_public_sample.json").read_text())[
        "results"
    ]
