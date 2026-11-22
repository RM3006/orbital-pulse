"""Nonempty analytical contracts and the release-consistent, read-only app boundary."""

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from launch_atlas.app.data import COLUMNS, AppSettings, Edition, EditionReader, edition_sql
from launch_atlas.app.main import create_app
from launch_atlas.app.model import Atlas, date_label, rate, window_label, window_position
from launch_atlas.app.views import (
    annual_figure,
    booster_history,
    manifest,
    overview_parts,
    page_slice,
    planned_visual,
    site_map,
    site_readout,
    workspace,
)

RID = "sla-release-v1-" + "a" * 24
SID = "sla-input-v1-" + "b" * 64


@pytest.fixture
def edition() -> Edition:
    def daily(day: str, key: int, success: int, failure: int, unresolved: int = 0):
        return dict(
            launch_date_utc=day,
            launch_year_utc=int(day[:4]),
            provider_id=121,
            configuration_id=key,
            site_id=12,
            attempt_count=success + failure + unresolved,
            success_count=success,
            failure_count=failure,
            partial_failure_count=0,
            classified_outcome_count=success + failure,
            unresolved_count=unresolved,
        )

    def upcoming(key: str, basis: str, start: str, end: str):
        return dict(
            launch_id=key,
            net_utc=start,
            source_status_name="To Be Confirmed",
            scope_classification="unknown" if key == "future" else "orbital",
            interval_basis=basis,
            schedule_earliest_utc=start,
            schedule_latest_exclusive_utc=end,
            horizon_start_utc="2026-11-14T11:12:19Z",
            horizon_end_exclusive_utc="2027-02-12T11:12:19Z",
        )

    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in COLUMNS}
    tables.update(
        {
            "mart_launch_attempt_daily": [
                daily("2020-01-01", 1, 1, 0),
                daily("2025-11-13", 1, 3, 1),
                daily("2025-11-14", 2, 1, 0),
                daily("2026-11-13", 2, 0, 0, 2),
            ],
            "core_dim_provider": [dict(provider_id=121, name="SpaceX")],
            "core_dim_vehicle_configuration": [
                dict(configuration_id=1, name="Same name"),
                dict(configuration_id=2, name="Same name"),
            ],
            "core_dim_vehicle_family": [
                dict(family_id=7, name="Family"),
                dict(family_id=8, name="Other family"),
            ],
            "core_configuration_family": [
                dict(configuration_id=1, family_id=7),
                dict(configuration_id=1, family_id=8),
                dict(configuration_id=2, family_id=7),
            ],
            "mart_family_outcomes_daily": [{**daily("2025-11-13", 1, 3, 1), "family_id": 7}],
            "mart_data_coverage": [
                dict(
                    current_launch_records=4,
                    orbital_attempt_records=8,
                    unknown_scope_records=1,
                    unsupported_upcoming_precision_records=2,
                    attempts_with_recorded_stage_flights=2,
                    latest_source_update_utc=None,
                    latest_observation_utc=None,
                    last_complete_refresh_utc="2026-11-14T11:12:19Z",
                    history_complete=True,
                )
            ],
            "mart_site_cadence": [
                dict(
                    launch_id="first",
                    site_id=12,
                    site_name="Cape Canaveral, USA",
                    latitude=28.5,
                    longitude=-80.5,
                    launch_date_utc="2026-11-13",
                    attempt_count=1,
                    observed_gap_days=4.5,
                    history_complete=True,
                )
            ],
            "mart_landing": [
                dict(
                    landing_id=1,
                    attempted_landing_count=1,
                    known_landing_outcome_count=1,
                    successful_landing_count=1,
                    unknown_landing_outcome_count=0,
                )
            ],
            "mart_upcoming_schedule": [
                upcoming("future", "year", "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"),
                upcoming("second", "minute", "2026-11-15T04:30:00Z", "2026-11-15T04:31:00Z"),
            ],
            "launch_labels": [
                dict(
                    launch_id=key,
                    name=key.title(),
                    provider_id=121,
                    configuration_id=2 if key == "second" else 1,
                    net_utc="2026-11-13T00:00:00Z",
                    observed_at_utc="2026-11-14T11:12:19Z",
                    is_orbital_attempt=key != "future",
                    source_status_id=3,
                    source_status_name="Launch Successful",
                    outcome="success",
                )
                for key in ("first", "second", "future", "bare")
            ],
            "mart_booster_flight": [
                dict(
                    launch_id=key,
                    booster_id=bid,
                    serial_number=serial,
                    launch_net_utc=stamp,
                    source_flight_number=flight,
                    turnaround_days=gap,
                    turnaround_supported=gap is not None,
                    identifiable_flight_count=1,
                    reused_flight_count=int(flight > 1),
                    reuse_known_count=1,
                )
                for key, bid, serial, stamp, flight, gap in (
                    ("first", 101, "B1067", "2025-01-01T00:00:00Z", 1, None),
                    ("second", 101, "B1067", "2026-11-13T00:00:00Z", 2, 681.0),
                    ("second", 84, "B1063", "2026-11-13T00:00:00Z", 1, None),
                )
            ],
        }
    )
    for name, rows in tables.items():
        for row in rows:
            row["input_snapshot_id"] = SID
            if name != "launch_labels":
                row["release_id"] = RID
    return Edition(RID, SID, "2026-11-14T11:12:19Z", tables)


def test_outcomes_keep_entity_keys_missingness_and_classified_denominator(edition):
    rows = Atlas(edition).outcomes("configuration")
    assert len(rows) == 2
    assert {r["id"] for r in rows} == {1, 2}
    assert rows[0]["success_count"] == 4
    assert rows[1]["unresolved_count"] == 2
    assert rate(4, 5) == "80.0%"
    assert rate(0, 0) == "—"
    family = Atlas(edition).outcomes("family")
    assert len(family) == 1
    assert family[0]["attempt_count"] == 4


def test_annual_excludes_incomplete_year_and_ytd_excludes_cut_day(edition):
    atlas = Atlas(edition)
    assert atlas.ytd[2025] == 4  # The 14 November row is outside the comparable full-day cut.
    assert atlas.annual[2025] == 5
    fig = annual_figure(atlas)
    assert max(fig.to_plotly_json()["data"][0]["x"]) == 2025
    assert list(fig.to_plotly_json()["data"][0]["y"])[-1] == 5
    assert min(annual_figure(atlas, 1957).to_plotly_json()["data"][0]["x"]) == 1957


def test_coarse_windows_clip_to_shared_horizon_without_false_precision(edition):
    atlas = Atlas(edition)
    broad = next(r for r in atlas.upcoming if r["interval_basis"] == "year")
    left, width = window_position(broad)
    assert left == 0
    assert 0 < width < 100
    assert window_label(broad) == "2026 · year only"
    precise = next(row for row in atlas.upcoming if row["interval_basis"] == "minute")
    assert window_label(precise) == "15 Nov 2026 · 04:30"
    count, _ = manifest(atlas, "year")
    assert count.startswith("1 entry overlaps")
    assert atlas.precision_counts == {"minute": 1, "year": 1}
    broken = {**broad, "schedule_earliest_utc": "2030-01-01T00:00:00Z"}
    with pytest.raises(ValueError, match="overlap"):
        window_position(broken)


def test_zero_months_supported_gaps_and_booster_scope(edition):
    atlas = Atlas(edition)
    site = atlas.featured_sites[0]
    assert len(atlas.months) == 35
    assert site["monthly"][atlas.months[0]] == 0
    assert site["median"] == 4.5
    assert booster_history(atlas, "101")[0] == (
        "2 recorded flights · 1 supported turnaround intervals"
    )
    assert booster_history(atlas, "84")[0].startswith("1 recorded flights")
    assert len(atlas.tables["mart_landing"]) == 1
    assert atlas.identifiable == 3


def test_all_five_pages_construct_without_credentials(edition):
    app = create_app()
    assert app.server.test_client().get("/healthz").status_code == 200
    nodes = workspace(Atlas(edition))
    chapters = nodes[2].children
    assert len(chapters) == 5
    assert [c.hidden for c in chapters] == [False, True, True, True, True]
    assert "14 Nov 2026" in str(nodes[0].children[1].children)
    assert date_label("2026-11-14T01:30:00+02:00") == "13 Nov 2026"


class FakeClient:
    def __init__(self, edition: Edition):
        self.edition = edition
        self.queries: list[Any] = []

    def list_rows(self, table: str, **kwargs):
        if table.endswith("active_release"):
            return [SimpleNamespace(pointer_key="active", release_id=RID, updated_at="today")]
        return [
            SimpleNamespace(
                release_id=RID,
                validated=True,
                manifest_json=json.dumps(
                    {
                        "release_id": RID,
                        "input_snapshot_id": SID,
                        "validated": True,
                        "analysis_as_of_utc": self.edition.analysis_as_of,
                        "reporting_row_counts": {k: len(v) for k, v in self.edition.tables.items()},
                    }
                ),
            )
        ]

    def query(self, sql: str, job_config, **kwargs):
        assert kwargs["api_method"] == "QUERY"  # jobs.query supports the read-only OAuth scope.
        self.queries.append(job_config)
        rows = [
            SimpleNamespace(relation=name, payload=json.dumps(row))
            for name, values in self.edition.tables.items()
            for row in values
        ]
        return SimpleNamespace(
            statement_type="SELECT",
            total_bytes_processed=100,
            total_bytes_billed=10_000_000,
            result=lambda **_: rows,
        )


def test_reader_pins_release_reuses_cache_and_caps_live_queries(edition):
    client = FakeClient(edition)
    reader = EditionReader(client=client)
    first = reader.latest()
    assert reader.latest() is first
    assert len(client.queries) == 2  # One dry run and one live SELECT for the entire edition.
    assert client.queries[0].dry_run is True
    assert client.queries[1].maximum_bytes_billed == 250_000_000
    assert first.tables["mart_upcoming_schedule"] == edition.tables["mart_upcoming_schedule"]
    assert "retained_mart_upcoming_schedule" in edition_sql(AppSettings(), SID)
    with pytest.raises(ValueError, match="snapshot"):
        edition_sql(AppSettings(), SID + "`; DROP TABLE x")


def test_mixed_release_or_duplicate_labels_never_enter_cache(edition):
    tables = {name: [dict(row) for row in values] for name, values in edition.tables.items()}
    tables["mart_upcoming_schedule"][0]["release_id"] = "another-release"
    with pytest.raises(ValueError, match="Mixed reporting"):
        EditionReader(client=FakeClient(replace(edition, tables=tables))).latest()
    tables["mart_upcoming_schedule"][0]["release_id"] = RID
    tables["launch_labels"].append(dict(tables["launch_labels"][0]))
    with pytest.raises(ValueError, match="not unique"):
        EditionReader(client=FakeClient(replace(edition, tables=tables))).latest()
    tables["launch_labels"] = [dict(row) for row in edition.tables["launch_labels"]]
    tables["launch_labels"][0]["launch_id"] = "unrelated"
    with pytest.raises(ValueError, match="labels are missing"):
        EditionReader(client=FakeClient(replace(edition, tables=tables))).latest()


def test_serial_context_uses_its_recorded_flights_without_filtering_them(edition):
    atlas = Atlas(edition)
    assert dict(atlas.entity_context("configuration", 1)) == {
        "Providers": ["SpaceX"],
        "Families": ["Family", "Other family"],
    }
    assert dict(atlas.booster_context("101")) == {
        "Provider": ["SpaceX"],
        "Configuration": ["Same name", "Same name"],
        "Vehicle families": ["Family", "Other family"],
    }
    assert dict(atlas.booster_context("84"))["Vehicle families"] == ["Family"]
    assert len(atlas.boosters[101]) == 2
    assert len(atlas.boosters[84]) == 1
    assert atlas.identifiable == 3


def test_planned_counts_keep_unique_launches_unknowns_and_overlapping_families(edition):
    extra = {**edition.tables["mart_upcoming_schedule"][0], "launch_id": "unknown"}
    labels = [
        *edition.tables["launch_labels"],
        {
            "launch_id": "unknown",
            "provider_id": None,
            "configuration_id": None,
            "name": "Unknown context",
        },
    ]
    tables = {
        **edition.tables,
        "launch_labels": labels,
        "mart_upcoming_schedule": [*edition.tables["mart_upcoming_schedule"], extra],
    }
    atlas = Atlas(replace(edition, tables=tables))
    providers, total = atlas.planned_counts()
    assert total == 3
    assert {row["id"]: row["count"] for row in providers} == {121: 2, None: 1}
    configs, total = atlas.planned_counts("configuration", "121")
    assert total == 2
    assert {row["id"]: row["count"] for row in configs} == {1: 1, 2: 1}
    families, total = atlas.planned_counts("family")
    assert total == 3
    assert {row["id"]: row["count"] for row in families} == {7: 2, 8: 1, None: 1}
    assert atlas.planned_counts("provider", "121", "year")[1] == 1
    assert atlas.planned_counts("configuration", "None")[0][0]["name"] == "Unknown configuration"
    assert "do not sum" in planned_visual(atlas, "family")[1]


def test_comparison_years_pagination_and_reported_window_ascending(edition):
    atlas = Atlas(edition)
    parts = overview_parts(atlas, 2025, 2020)
    assert "from 5 in 2025 to 1 in 2020" in str(parts[0])
    assert page_slice(234, 1, 25) == (1, 25, 50, "26–50 of 234 rows")
    assert page_slice(3, 9, 25) == (0, 0, 3, "1–3 of 3 rows")
    assert page_slice(234, 0, 0)[2] == 234
    row = {
        **edition.tables["mart_upcoming_schedule"][0],
        "launch_id": "later-coarse",
        "schedule_earliest_utc": "2027-01-01T00:00:00Z",
        "schedule_latest_exclusive_utc": "2028-01-01T00:00:00Z",
    }
    tables = {**edition.tables, "mart_upcoming_schedule": [*atlas.upcoming, row]}
    ordered = Atlas(replace(edition, tables=tables)).upcoming
    assert [r["launch_id"] for r in ordered] == ["future", "second", "later-coarse"]


def test_map_uses_real_coordinates_and_zoomable_maplibre_view(edition):
    atlas = Atlas(edition)
    fig = site_map(atlas, "12", "site").to_plotly_json()
    assert fig["data"][0]["type"] == "scattermap"
    assert list(fig["data"][0]["lat"]) == [28.5]
    assert list(fig["data"][0]["lon"]) == [-80.5]
    assert fig["layout"]["map"]["center"] == {"lat": 28.5, "lon": -80.5}
    assert fig["layout"]["map"]["zoom"] == 10
    assert fig["layout"]["meta"]["atlas_map_view"] == "site"


def test_map_includes_all_located_sites_in_period_beyond_heatmap_preview(edition):
    base = edition.tables["mart_site_cadence"][0]
    sites = [
        {
            **base,
            "launch_id": f"site-launch-{i}",
            "site_id": 100 + i,
            "site_name": f"Site {i}",
            "latitude": 20 + i,
            "longitude": 30 + i,
        }
        for i in range(9)
    ]
    sites.extend(
        [
            {
                **base,
                "launch_id": "unlocated",
                "site_id": 200,
                "site_name": "Unlocated",
                "latitude": None,
            },
            {
                **base,
                "launch_id": "old",
                "site_id": 201,
                "site_name": "Old site",
                "launch_date_utc": "2023-12-31",
            },
        ]
    )
    atlas = Atlas(replace(edition, tables={**edition.tables, "mart_site_cadence": sites}))
    assert len(atlas.mapped_sites) == 9
    assert len(atlas.featured_sites) == 6
    assert len(atlas.sites) == 10  # Unlocated activity stays in the supporting totals/table.
    figure = site_map(atlas, "108", "site").to_plotly_json()
    assert set(figure["data"][0]["customdata"]) == {str(100 + i) for i in range(9)}
    assert figure["layout"]["map"]["center"] == {"lat": 28, "lon": 38}
    assert site_readout(atlas, "108").startswith("Site 8")
