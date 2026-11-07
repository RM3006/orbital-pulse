import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from launch_atlas.clients.ll2 import safe_url
from launch_atlas.config import Settings
from launch_atlas.pipeline import run_plan
from launch_atlas.storage.state import StateStore
from launch_atlas.windows import (
    create_backfill_plan,
    create_reconcile_plan,
    create_refresh_plan,
)


def test_backfill_and_refresh_plans_freeze_windows(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    settings = Settings(data_dir=tmp_path)
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    history = create_backfill_plan(state, settings, "history-1957", 1957, 1957, frozen_as_of=now)
    assert history["windows"] == 2
    rows = state.plan_windows("history-1957")
    assert [row["pass_number"] for row in rows] == [1, 2]
    assert json.loads(rows[0]["query_json"])["page_size"] == 100

    refresh = create_refresh_plan(state, settings, "refresh-test", frozen_as_of=now)
    assert refresh["windows"] == 3
    correction = state.window("refresh-test-corrections-001")
    assert correction["start_utc"] == "2026-10-08T11:55:00+00:00"
    query = json.loads(correction["query_json"])
    assert query["include_suborbital"] is True
    assert query["last_updated"] is True
    upcoming = state.window("refresh-test-upcoming")
    assert json.loads(upcoming["query_json"])["start"].startswith("2026-01-01")
    assert json.loads(upcoming["query_json"])["end"].startswith("2028-01-01")


def test_broadened_scope_is_only_allowed_for_bounded_corrections() -> None:
    source = "https://ll.thespacedevs.com/2.3.0/launches/previous/"
    query = (
        "?limit=10&mode=detailed&include_suborbital=true&ordering=-last_updated"
        "&last_updated__gte=2026-11-01T00%3A00%3A00Z"
        "&last_updated__lte=2026-11-07T00%3A00%3A00Z"
    )
    assert safe_url(source + query, "https://ll.thespacedevs.com/2.3.0/") == source + query
    for unsafe in (
        source + "?limit=10&mode=detailed&include_suborbital=true",
        "https://ll.thespacedevs.com/2.3.0/launches/upcoming/" + query[query.index("?") :],
    ):
        try:
            safe_url(unsafe, "https://ll.thespacedevs.com/2.3.0/")
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe broad query was accepted")


def test_two_pass_window_runner_shares_budget_and_reconciles(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings = Settings(data_dir=tmp_path, max_requests=2)
    state = StateStore(tmp_path)
    create_backfill_plan(
        state,
        settings,
        "mini-history",
        1957,
        1957,
        frozen_as_of=datetime(2026, 11, 7, tzinfo=UTC),
    )
    page = json.dumps({"count": 1, "next": None, "results": [records[2]]}).encode()
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=page)

    result = run_plan(settings, "mini-history", transport=httpx.MockTransport(handler))
    assert result["complete"]
    assert result["requests_this_invocation"] == 2
    assert calls == 2
    windows = state.plan_windows("mini-history")
    assert all(row["extraction_status"] == "committed" for row in windows)
    assert windows[1]["reconciled"] == 1


def test_unstable_window_gets_a_third_pass_in_a_child_plan(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings = Settings(data_dir=tmp_path, max_requests=2)
    state = StateStore(tmp_path)
    create_backfill_plan(
        state,
        settings,
        "unstable",
        1957,
        1957,
        frozen_as_of=datetime(2026, 11, 7, tzinfo=UTC),
    )
    first_record = json.loads(json.dumps(records[2]))
    second_record = json.loads(json.dumps(records[3]))
    second_record["last_updated"] = "2026-11-06T00:00:00Z"
    pages = [first_record, second_record]

    def handler(_: httpx.Request) -> httpx.Response:
        record = pages.pop(0) if pages else second_record
        page = json.dumps({"count": 1, "next": None, "results": [record]}).encode()
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=page)

    first_passes = run_plan(settings, "unstable", transport=httpx.MockTransport(handler))
    assert first_passes["complete"] is False
    assert first_passes["windows_unreconciled"] == 1
    child = create_reconcile_plan(state, settings, "unstable", "unstable-retry")
    assert child["windows"] == 1
    retry = run_plan(
        settings.model_copy(update={"max_requests": 1}),
        "unstable-retry",
        transport=httpx.MockTransport(handler),
    )
    assert retry["complete"]
    assert state.plan_windows("unstable-retry")[0]["reconciled"] == 1


def test_dense_annual_window_splits_both_passes_into_months(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    settings = Settings(data_dir=tmp_path, max_requests=1)
    state = StateStore(tmp_path)
    start, end = "1957-01-01T00:00:00+00:00", "1958-01-01T00:00:00+00:00"
    windows = []
    for pass_number in (1, 2):
        window_id = f"dense-1957-pass-{pass_number}"
        query = {
            "window": "history",
            "start": start,
            "end": end,
            "include_suborbital": False,
            "page_size": 2,
        }
        windows.append(
            {
                "window_id": window_id,
                "stream_id": "history-1957",
                "start_utc": start,
                "end_utc": end,
                "pass_number": pass_number,
                "query": query,
                "initial_url": (
                    "https://ll.thespacedevs.com/2.3.0/launches/previous/?"
                    "limit=2&mode=detailed&include_suborbital=false"
                ),
            }
        )
    state.create_plan(
        "dense",
        "backfill",
        "2026-11-07T00:00:00Z",
        {"page_size": 2, "dense_source_count_split_threshold": 1},
        windows,
    )
    body = json.dumps({"count": 2, "next": None, "results": records[2:4]}).encode()
    result = run_plan(
        settings,
        "dense",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"Content-Type": "application/json"}, content=body
            )
        ),
    )
    assert result["results"][0]["status"] == "split_into_months"
    windows = state.plan_windows("dense")
    assert sum(row["extraction_status"] == "split" for row in windows) == 2
    assert (
        sum(row["parent_window_id"] is not None and row["pass_number"] == 1 for row in windows)
        == 12
    )
    assert state.summary()["launch_current"] == 0
