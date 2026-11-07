from datetime import UTC, datetime

from launch_atlas.time_precision import calendar_envelope, schedule_interval


def test_calendar_precision_uses_complete_utc_periods() -> None:
    year = schedule_interval(datetime(2028, 12, 31, 12, tzinfo=UTC), {"id": 14, "name": "Year"})
    assert year.earliest_utc == datetime(2028, 1, 1, tzinfo=UTC)
    assert year.latest_exclusive_utc == datetime(2029, 1, 1, tzinfo=UTC)
    assert year.overlaps(datetime(2028, 12, 30, tzinfo=UTC), datetime(2029, 1, 2, tzinfo=UTC))
    assert not year.overlaps(datetime(2029, 1, 1, tzinfo=UTC), datetime(2029, 2, 1, tzinfo=UTC))


def test_quarter_half_year_and_leap_day_boundaries() -> None:
    cases = [
        (
            datetime(2028, 12, 31, tzinfo=UTC),
            {"id": 11, "name": "Quarter 4"},
            datetime(2028, 10, 1, tzinfo=UTC),
            datetime(2029, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2028, 12, 31, tzinfo=UTC),
            {"id": 13, "name": "Year Half 2"},
            datetime(2028, 7, 1, tzinfo=UTC),
            datetime(2029, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2028, 2, 29, 15, 12, 22, tzinfo=UTC),
            {"id": 5, "name": "Day"},
            datetime(2028, 2, 29, tzinfo=UTC),
            datetime(2028, 3, 1, tzinfo=UTC),
        ),
    ]
    for net, precision, earliest, latest in cases:
        interval = schedule_interval(net, precision)
        assert (interval.earliest_utc, interval.latest_exclusive_utc) == (earliest, latest)


def test_unknown_or_inconsistent_precision_is_not_guessed() -> None:
    net = datetime(2026, 12, 31, tzinfo=UTC)
    assert not schedule_interval(net, {"id": 999, "name": "Year"}).precision_supported
    assert not schedule_interval(net, {"id": 14, "name": "Day"}).precision_supported
    assert not schedule_interval(net, None).precision_supported


def test_calendar_envelope_includes_both_years_of_cross_year_horizon() -> None:
    start, end = calendar_envelope(
        datetime(2026, 11, 1, tzinfo=UTC), datetime(2027, 2, 1, tzinfo=UTC)
    )
    assert start == datetime(2026, 1, 1, tzinfo=UTC)
    assert end == datetime(2028, 1, 1, tzinfo=UTC)
