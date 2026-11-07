"""Frozen refresh and historical window planning."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from launch_atlas.config import Settings
from launch_atlas.storage.state import StateStore
from launch_atlas.time_precision import calendar_envelope


def _url(
    settings: Settings,
    *,
    window: str,
    start: datetime,
    end: datetime,
    page_size: int,
    include_suborbital: bool = False,
    last_updated: bool = False,
) -> str:
    endpoint = "launches/upcoming/" if window == "upcoming" else "launches/previous/"
    query: dict[str, str | int] = {
        "limit": page_size,
        "mode": "detailed",
        "include_suborbital": str(include_suborbital).lower(),
        "ordering": "-last_updated" if last_updated else ("-net" if window == "recent" else "net"),
    }
    query["last_updated__gte" if last_updated else "net__gte"] = start.isoformat()
    query["last_updated__lte" if last_updated else "net__lt"] = end.isoformat()
    return settings.base_url + endpoint + "?" + urlencode(query)


def create_backfill_plan(
    state: StateStore,
    settings: Settings,
    plan_id: str,
    start_year: int,
    end_year: int,
    *,
    frozen_as_of: datetime | None = None,
) -> dict[str, Any]:
    now = frozen_as_of or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Plan cutoff must be timezone-aware")
    if not 1957 <= start_year <= end_year <= now.astimezone(UTC).year:
        raise ValueError("Choose 1957 <= start-year <= end-year <= current UTC year")
    config = {
        "page_size": 100,
        "parser_version": "2",
        "scope": "include_suborbital=false; unknown mission orbit excluded from facts",
        "ordering": "net",
        "reconciliation_passes": 2,
        "dense_source_count_split_threshold": 250,
    }
    windows = []
    for year in range(start_year, end_year + 1):
        start = datetime(year, 1, 1, tzinfo=UTC)
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
        for pass_number in (1, 2):
            window_id = f"{plan_id}-{year}-pass-{pass_number}"
            windows.append(
                {
                    "window_id": window_id,
                    "stream_id": f"history-{year}",
                    "start_utc": start.isoformat(),
                    "end_utc": end.isoformat(),
                    "pass_number": pass_number,
                    "query": {
                        "window": "history",
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "include_suborbital": False,
                        "page_size": 100,
                    },
                    "initial_url": _url(
                        settings,
                        window="history",
                        start=start,
                        end=end,
                        page_size=100,
                    ),
                }
            )
    state.create_plan(plan_id, "backfill", now.isoformat(), config, windows)
    return {"plan_id": plan_id, "plan_type": "backfill", "windows": len(windows), "passes": 2}


def create_refresh_plan(
    state: StateStore,
    settings: Settings,
    plan_id: str,
    *,
    frozen_as_of: datetime | None = None,
) -> dict[str, Any]:
    now = (frozen_as_of or datetime.now(UTC)).astimezone(UTC)
    cutoff = now - timedelta(minutes=5)
    cursors = state.coverage_cursors()
    previous_cursor = cursors.get("corrections")
    correction_start = (
        datetime.fromisoformat(previous_cursor) - timedelta(hours=48)
        if previous_cursor
        else cutoff - timedelta(days=30)
    )
    recent_start, recent_end = now - timedelta(days=30), now
    upcoming_start, upcoming_end = calendar_envelope(now, now + timedelta(days=90))
    components = [
        ("recent", "recent", recent_start, recent_end, False, False),
        ("upcoming", "upcoming", upcoming_start, upcoming_end, False, False),
    ]
    cursor, correction_part = correction_start, 1
    while cursor < cutoff:
        part_end = min(cursor + timedelta(days=30), cutoff)
        components.append(
            (f"corrections-{correction_part:03d}", "history", cursor, part_end, True, True)
        )
        cursor = part_end
        correction_part += 1
    windows = []
    for stream, window, start, end, include_suborbital, last_updated in components:
        windows.append(
            {
                "window_id": f"{plan_id}-{stream}",
                "stream_id": stream.split("-")[0],
                "start_utc": start.isoformat(),
                "end_utc": end.isoformat(),
                "query": {
                    "window": window,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "include_suborbital": include_suborbital,
                    "last_updated": last_updated,
                    "page_size": 50,
                },
                "initial_url": _url(
                    settings,
                    window=window,
                    start=start,
                    end=end,
                    page_size=50,
                    include_suborbital=include_suborbital,
                    last_updated=last_updated,
                ),
            }
        )
    config = {
        "page_size": 50,
        "parser_version": "2",
        "recent_lookback_days": 30,
        "upcoming_horizon_days": 90,
        "correction_overlap_hours": 48,
        "correction_cutoff_lag_minutes": 5,
        "correction_scope": "include_suborbital=true; bounded last-updated query, split at 30 days",
    }
    state.create_plan(plan_id, "refresh", now.isoformat(), config, windows)
    return {
        "plan_id": plan_id,
        "plan_type": "refresh",
        "windows": len(windows),
        "correction_windows": correction_part - 1,
    }


def create_reconcile_plan(
    state: StateStore,
    settings: Settings,
    parent_plan_id: str,
    reconcile_plan_id: str,
) -> dict[str, Any]:
    """Schedule another pass only for completed partitions whose content sets differ."""
    parent_plan = state.plan(parent_plan_id)
    if parent_plan["plan_type"] not in ("backfill", "reconcile"):
        raise ValueError("Only history or reconciliation plans can receive another pass")
    windows = []
    for prior in state.unreconciled_windows(parent_plan_id):
        query = json.loads(prior["query_json"])
        start, end = datetime.fromisoformat(query["start"]), datetime.fromisoformat(query["end"])
        pass_number = prior["pass_number"] + 1
        windows.append(
            {
                "window_id": f"{reconcile_plan_id}-{start.year}-pass-{pass_number}",
                "parent_window_id": prior["window_id"],
                "stream_id": prior["stream_id"],
                "start_utc": prior["start_utc"],
                "end_utc": prior["end_utc"],
                "pass_number": pass_number,
                "query": query,
                "initial_url": _url(
                    settings,
                    window=query["window"],
                    start=start,
                    end=end,
                    page_size=int(query["page_size"]),
                    include_suborbital=bool(query["include_suborbital"]),
                    last_updated=bool(query.get("last_updated", False)),
                ),
            }
        )
    if not windows:
        raise ValueError("No completed unreconciled windows are available")
    config = {**parent_plan["config"], "parent_plan_id": parent_plan_id}
    state.create_plan(
        reconcile_plan_id,
        "reconcile",
        parent_plan["frozen_as_of_utc"],
        config,
        windows,
    )
    return {
        "plan_id": reconcile_plan_id,
        "parent_plan_id": parent_plan_id,
        "windows": len(windows),
    }


def split_history_year(state: StateStore, settings: Settings, parent: dict[str, Any]) -> int:
    """Split all not-yet-committed passes of one dense year into monthly windows."""
    query = json.loads(parent["query_json"])
    start, end = datetime.fromisoformat(query["start"]), datetime.fromisoformat(query["end"])
    if start.month != 1 or start.day != 1 or end != start.replace(year=start.year + 1):
        raise ValueError("Only a complete calendar-year history window can split by month")
    plan_windows = state.plan_windows(parent["plan_id"])
    passes = sorted(
        {
            row["pass_number"]
            for row in plan_windows
            if row["stream_id"] == parent["stream_id"]
            and row["start_utc"] == parent["start_utc"]
            and row["end_utc"] == parent["end_utc"]
        }
    )
    children = []
    cursor = start
    while cursor < end:
        next_month = cursor.replace(
            year=cursor.year + (cursor.month == 12), month=cursor.month % 12 + 1
        )
        for pass_number in passes:
            child_query = {
                **query,
                "start": cursor.isoformat(),
                "end": next_month.isoformat(),
            }
            children.append(
                {
                    "window_id": (
                        f"{parent['plan_id']}-{cursor.year}-{cursor.month:02d}-pass-{pass_number}"
                    ),
                    "stream_id": parent["stream_id"],
                    "start_utc": cursor.isoformat(),
                    "end_utc": next_month.isoformat(),
                    "pass_number": pass_number,
                    "query": child_query,
                    "initial_url": _url(
                        settings,
                        window=query["window"],
                        start=cursor,
                        end=next_month,
                        page_size=int(query["page_size"]),
                        include_suborbital=bool(query["include_suborbital"]),
                        last_updated=bool(query.get("last_updated", False)),
                    ),
                }
            )
        cursor = next_month
    state.split_window(parent["window_id"], children)
    return len(children)
