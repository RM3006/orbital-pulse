"""Pure, grain-aware presentation calculations for the Printed Atlas."""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any

from launch_atlas.app.data import Edition

PRECISIONS = {
    "second": "Second",
    "minute": "Minute",
    "hour": "Hour",
    "day": "Day",
    "month": "Month",
    "quarter": "Quarter",
    "half_year": "Half-year",
    "year": "Year",
}
OUTCOMES = (
    "attempt_count",
    "success_count",
    "failure_count",
    "partial_failure_count",
    "classified_outcome_count",
    "unresolved_count",
)


def utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    result = datetime.fromisoformat(value.replace(" UTC", "+00:00").replace("Z", "+00:00"))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def date_label(value: str | date | datetime) -> str:
    if isinstance(value, str):
        value = utc(value)
    return f"{value.day} {value:%b %Y}"


def number(value: Any) -> str:
    return "—" if value is None else f"{value:,}"


def signed(value: int) -> str:
    return f"{'+' if value > 0 else '−' if value < 0 else ''}{abs(value):,}"


def rate(success: int, classified: int) -> str:
    return f"{100 * success / classified:.1f}%" if classified else "—"


def window_label(row: dict[str, Any]) -> str:
    start, end = utc(row["schedule_earliest_utc"]), utc(row["schedule_latest_exclusive_utc"])
    basis = row["interval_basis"]
    if basis == "year":
        return f"{start.year} · year only"
    if basis == "half_year":
        return f"{'First' if start.month <= 6 else 'Second'} half {start.year}"
    if basis == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if basis == "month":
        return start.strftime("%b %Y")
    if basis == "day":
        return f"{date_label(start)} · day only"
    if basis == "hour":
        suffix = (
            end.strftime("%H:%M")
            if end.date() == start.date()
            else (f"{date_label(end)} {end:%H:%M}")
        )
        return f"{date_label(start)} · {start:%H:%M}–{suffix}"
    return f"{date_label(start)} · {start.strftime('%H:%M:%S' if basis == 'second' else '%H:%M')}"


def window_position(row: dict[str, Any]) -> tuple[float, float]:
    start, end = utc(row["horizon_start_utc"]), utc(row["horizon_end_exclusive_utc"])
    left = max(start, utc(row["schedule_earliest_utc"]))
    right = min(end, utc(row["schedule_latest_exclusive_utc"]))
    if end <= start or left >= right:
        raise ValueError("Manifest interval does not overlap its report horizon")
    seconds = (end - start).total_seconds()
    return 100 * (left - start).total_seconds() / seconds, (
        100 * (right - left).total_seconds() / seconds
    )


class Atlas:
    def __init__(self, edition: Edition) -> None:
        self.edition = edition
        self.as_of = utc(edition.analysis_as_of)
        self.tables = edition.tables
        self.coverage = self.tables["mart_data_coverage"][0]
        self.labels = {r["launch_id"]: r for r in self.tables["launch_labels"]}
        self.providers = {r["provider_id"]: r["name"] for r in self.tables["core_dim_provider"]}
        self.configurations = {
            r["configuration_id"]: r["name"] for r in self.tables["core_dim_vehicle_configuration"]
        }
        self.families = {r["family_id"]: r["name"] for r in self.tables["core_dim_vehicle_family"]}
        self.configuration_families: dict[Any, set[Any]] = defaultdict(set)
        for row in self.tables["core_configuration_family"]:
            self.configuration_families[row["configuration_id"]].add(row["family_id"])
        self.configuration_providers: dict[Any, set[Any]] = defaultdict(set)
        self.annual: Counter[int] = Counter()
        self.ytd: Counter[int] = Counter()
        self.provider_year: Counter[tuple[Any, int]] = Counter()
        for row in self.tables["mart_launch_attempt_daily"]:
            day = date.fromisoformat(row["launch_date_utc"])
            count = row["attempt_count"]
            self.annual[day.year] += count
            self.provider_year[row["provider_id"], day.year] += count
            if day >= date(2000, 1, 1):
                self.configuration_providers[row["configuration_id"]].add(row["provider_id"])
            cutoff = date(
                day.year,
                self.as_of.month,
                min(self.as_of.day, calendar.monthrange(day.year, self.as_of.month)[1]),
            )
            if day < cutoff:
                self.ytd[day.year] += count
        self.last_year = self.as_of.year - 1
        self.comparison_years = list(range(min(self.annual, default=1957), self.last_year + 1))
        self.comparison_start = max(self.comparison_years[0], self.last_year - 5)
        self._make_sites()
        self._make_boosters()
        self.upcoming = sorted(
            self.tables["mart_upcoming_schedule"],
            key=lambda r: (
                utc(r["schedule_earliest_utc"]).timestamp(),
                utc(r["schedule_latest_exclusive_utc"]).timestamp(),
                r["launch_id"],
            ),
        )
        self.precision_counts = Counter(r["interval_basis"] for r in self.upcoming)
        horizons = {(r["horizon_start_utc"], r["horizon_end_exclusive_utc"]) for r in self.upcoming}
        if len(horizons) > 1:
            raise ValueError("A manifest cannot combine horizons")
        self.horizon = (
            tuple(utc(v) for v in next(iter(horizons)))
            if horizons
            else (self.as_of, self.as_of + timedelta(days=90))
        )

    def _make_sites(self) -> None:
        groups: dict[Any, dict[str, Any]] = {}
        last_month = self.as_of.year * 12 + self.as_of.month - 1
        self.months = [date(i // 12, i % 12 + 1, 1) for i in range(2024 * 12, last_month + 1)]
        for row in self.tables["mart_site_cadence"]:
            day = date.fromisoformat(row["launch_date_utc"])
            if day < date(2024, 1, 1):
                continue
            group = groups.setdefault(
                row["site_id"],
                {
                    "id": row["site_id"],
                    "name": row["site_name"] or "Unknown site",
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "attempts": 0,
                    "gaps": [],
                    "monthly": Counter(),
                },
            )
            group["attempts"] += row["attempt_count"]
            group["monthly"][day.replace(day=1)] += row["attempt_count"]
            if row["observed_gap_days"] is not None:
                group["gaps"].append(float(row["observed_gap_days"]))
        self.sites = sorted(
            groups.values(), key=lambda g: (-g["attempts"], g["name"], str(g["id"]))
        )
        for group in self.sites:
            group["median"] = median(group["gaps"]) if group["gaps"] else None
        self.mapped_sites = [
            g for g in self.sites if g["latitude"] is not None and g["longitude"] is not None
        ]
        self.featured_sites = self.mapped_sites[:6]

    def _make_boosters(self) -> None:
        self.boosters: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        self.reused = self.reuse_known = self.identifiable = self.spacex_flights = 0
        for row in self.tables["mart_booster_flight"]:
            self.boosters[row["booster_id"]].append(row)
            self.reused += row["reused_flight_count"]
            self.reuse_known += row["reuse_known_count"]
            self.identifiable += row["identifiable_flight_count"]
            if self.labels.get(row["launch_id"], {}).get("provider_id") == 121:
                self.spacex_flights += row["identifiable_flight_count"]
        for rows in self.boosters.values():
            rows.sort(key=lambda r: (utc(r["launch_net_utc"]), r["launch_id"]))
        self.booster_options = sorted(
            self.boosters,
            key=lambda key: (
                -sum(r["identifiable_flight_count"] for r in self.boosters[key]),
                self.boosters[key][0]["serial_number"] or "",
                key,
            ),
        )

    def family_names(self, configuration_id: Any) -> str:
        return (
            ", ".join(
                sorted(
                    self.families.get(key) or "Unknown family"
                    for key in self.configuration_families.get(configuration_id, set())
                )
            )
            or "Unknown family"
        )

    def booster_context(self, selected: str) -> list[tuple[str, list[str]]]:
        rows = next((rows for key, rows in self.boosters.items() if str(key) == selected), [])
        launches = [self.labels.get(row["launch_id"], {}) for row in rows]
        provider_ids = {row.get("provider_id") for row in launches}
        configuration_ids = {row.get("configuration_id") for row in launches}
        family_ids = {
            fid
            for cid in configuration_ids
            for fid in (self.configuration_families.get(cid) or {None})
        }
        return [
            (
                "Provider",
                sorted(self.providers.get(key) or "Unknown provider" for key in provider_ids),
            ),
            (
                "Configuration",
                sorted(
                    self.configurations.get(key) or "Unknown configuration"
                    for key in configuration_ids
                ),
            ),
            (
                "Vehicle families",
                sorted(self.families.get(key) or "Unknown family" for key in family_ids),
            ),
        ]

    def planned_counts(
        self, group: str = "provider", provider: str = "", precision: str = ""
    ) -> tuple[list[dict[str, Any]], int]:
        """Distinct manifest launches per category; family bars may overlap."""
        groups: dict[Any, set[str]] = defaultdict(set)
        selected: set[str] = set()
        names = {
            "provider": self.providers,
            "configuration": self.configurations,
            "family": self.families,
        }[group]
        for row in self.upcoming:
            context = self.labels.get(row["launch_id"], {})
            if provider and str(context.get("provider_id")) != provider:
                continue
            if precision and row["interval_basis"] != precision:
                continue
            selected.add(row["launch_id"])
            key = context.get("provider_id" if group == "provider" else "configuration_id")
            keys = (self.configuration_families.get(key) or {None}) if group == "family" else {key}
            for category in keys:
                groups[category].add(row["launch_id"])
        rows = [
            {"id": key, "name": names.get(key) or f"Unknown {group}", "count": len(ids)}
            for key, ids in groups.items()
        ]
        return sorted(rows, key=lambda row: (-row["count"], row["name"], str(row["id"]))), len(
            selected
        )

    def entity_context(self, kind: str, key: Any) -> list[tuple[str, list[str]]]:
        configs = (
            {key}
            if kind == "configuration"
            else {cid for cid, families in self.configuration_families.items() if key in families}
            if kind == "family"
            else {
                cid for cid, providers in self.configuration_providers.items() if key in providers
            }
        )
        providers = {pid for cid in configs for pid in self.configuration_providers.get(cid, set())}
        families = {fid for cid in configs for fid in self.configuration_families.get(cid, set())}
        context = []
        if kind != "provider":
            context.append(
                (
                    "Providers",
                    sorted(self.providers.get(pid) or "Unknown provider" for pid in providers),
                )
            )
        if kind != "family":
            context.append(
                ("Families", sorted(self.families.get(fid) or "Unknown family" for fid in families))
            )
        if kind != "configuration":
            context.append(
                (
                    "Configurations",
                    sorted(
                        self.configurations.get(cid) or "Unknown configuration" for cid in configs
                    ),
                )
            )
        return context

    def outcomes(self, group: str) -> list[dict[str, Any]]:
        lookup = {
            "configuration": ("mart_launch_attempt_daily", "configuration_id", "configuration"),
            "provider": ("mart_launch_attempt_daily", "provider_id", "provider"),
            "family": ("mart_family_outcomes_daily", "family_id", "family"),
        }
        table, key, dimension = lookup[group]
        names = {
            r[key]: r["name"]
            for r in self.tables[
                {
                    "configuration": "core_dim_vehicle_configuration",
                    "provider": "core_dim_provider",
                    "family": "core_dim_vehicle_family",
                }[dimension]
            ]
        }
        groups: dict[Any, Counter[str]] = defaultdict(Counter)
        for row in self.tables[table]:
            if row["launch_date_utc"] >= "2000-01-01":
                groups[row[key]].update({field: row[field] for field in OUTCOMES})
        result = [
            {"id": key, "name": names.get(key) or "Unknown", **values}
            for key, values in groups.items()
        ]
        return sorted(result, key=lambda r: (-r["attempt_count"], r["name"], r["id"] or -1))

    def launch_name(self, launch_id: str) -> str:
        return self.labels.get(launch_id, {}).get("name") or "Unknown launch"
