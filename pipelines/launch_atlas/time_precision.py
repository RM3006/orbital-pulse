"""LL2 schedule precision intervals and manifest overlap semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class ScheduleInterval:
    earliest_utc: datetime | None
    latest_exclusive_utc: datetime | None
    interval_basis: str
    precision_supported: bool

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """Return interval overlap for a half-open requested horizon [start, end)."""
        if not self.precision_supported or self.earliest_utc is None:
            return False
        assert self.latest_exclusive_utc is not None
        return self.earliest_utc < end.astimezone(
            UTC
        ) and self.latest_exclusive_utc > start.astimezone(UTC)


def _next_month(value: datetime, months: int) -> datetime:
    index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(index, 12)
    return value.replace(
        year=year, month=month0 + 1, day=1, hour=0, minute=0, second=0, microsecond=0
    )


def schedule_interval(net: datetime | None, precision: dict[str, Any] | None) -> ScheduleInterval:
    """Interpret verified LL2 precision IDs; unknown or inconsistent enums stay unknown."""
    if net is None or net.tzinfo is None:
        return ScheduleInterval(None, None, "missing_or_naive_net", False)
    value = net.astimezone(UTC)
    if not precision:
        return ScheduleInterval(None, None, "missing_precision", False)
    precision_id, name = precision.get("id"), precision.get("name")
    if precision_id is None:
        return ScheduleInterval(None, None, "unknown_precision_id", False)
    try:
        precision_id = int(precision_id)
    except (TypeError, ValueError):
        return ScheduleInterval(None, None, "unknown_precision_id", False)
    expected = {
        0: "second",
        1: "minute",
        2: "hour",
        5: "day",
        7: "month",
        11: "quarter",
        13: "half_year",
        14: "year",
    }
    basis = expected.get(precision_id)
    if basis is None or not isinstance(name, str):
        return ScheduleInterval(None, None, "unsupported_precision", False)
    normalized_name = name.lower()
    valid_name = {
        0: normalized_name == "second",
        1: normalized_name == "minute",
        2: normalized_name == "hour",
        5: normalized_name == "day",
        7: normalized_name == "month",
        11: normalized_name.startswith("quarter ") and normalized_name[-1:] in "1234",
        13: normalized_name.startswith("year half ") and normalized_name[-1:] in "12",
        14: normalized_name == "year",
    }[precision_id]
    if not valid_name:
        return ScheduleInterval(None, None, "inconsistent_precision_name", False)
    if basis == "second":
        start = value.replace(microsecond=0)
        end = start + timedelta(seconds=1)
    elif basis == "minute":
        start = value.replace(second=0, microsecond=0)
        end = _next_minute(start)
    elif basis == "hour":
        start = value.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
    elif basis == "day":
        start = value.replace(hour=0, minute=0, second=0, microsecond=0)
        end = _next_day(start, 1)
    elif basis == "month":
        start = value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = _next_month(start, 1)
    elif basis == "quarter":
        quarter_number = int(normalized_name[-1])
        start_month = (quarter_number - 1) * 3 + 1
        start = value.replace(month=start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = _next_month(start, 3)
    elif basis == "half_year":
        half_number = int(normalized_name[-1])
        start = value.replace(
            month=1 if half_number == 1 else 7, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end = _next_month(start, 6)
    else:
        start = value.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
    if not start <= value < end:
        return ScheduleInterval(None, None, "net_outside_precision_period", False)
    return ScheduleInterval(start, end, basis, True)


def _next_minute(value: datetime) -> datetime:
    return value + timedelta(minutes=1)


def _next_day(value: datetime, days: int) -> datetime:
    return value + timedelta(days=days)


def calendar_envelope(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Whole-year acquisition envelope enclosing a nonempty UTC horizon."""
    first, last = start.astimezone(UTC), end.astimezone(UTC)
    if first >= last:
        raise ValueError("Expected a nonempty [start, end) horizon")
    lower = first.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    final_included = last - timedelta(microseconds=1)
    upper = final_included.replace(
        year=final_included.year + 1,
        month=1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return lower, upper
