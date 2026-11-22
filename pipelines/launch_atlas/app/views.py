"""Dash components matching the approved Printed Atlas HTML and CSS."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any, cast

import plotly.graph_objects as go
from dash import dcc, html

from launch_atlas.app.model import (
    PRECISIONS,
    Atlas,
    date_label,
    number,
    rate,
    signed,
    utc,
    window_label,
    window_position,
)

PAGES = {
    "overview": "Overview",
    "vehicles": "Providers & vehicles",
    "sites": "Spaceports",
    "reuse": "Reuse & landings",
    "schedule": "Upcoming & coverage",
}
ASSETS = Path(__file__).parent / "assets"


def control(label: str, ident: str, options: list[tuple[Any, str]], value: Any) -> Any:
    return html.Label(
        [
            label,
            html.Select(
                [
                    html.Option(text, value=str(key), selected=str(key) == str(value))
                    for key, text in options
                ],
                id=ident,
                **cast(dict[str, Any], {"data-store": ident + "-value"}),
            ),
        ],
        className="atlas-control",
        htmlFor=ident,
    )


def heading(title: str, note: str, widget: Any = None, note_id: str | None = None) -> Any:
    return html.Div(
        [
            html.Div(
                [
                    html.H3(title),
                    html.P(note, **cast(dict[str, Any], {"id": note_id} if note_id else {})),
                ]
            )
        ]
        + ([widget] if widget is not None else []),
        className="atlas-figure-head",
    )


def lede(page: str, label: str, title: Any, deck: str, side: Any = None) -> Any:
    return html.Div(
        [
            html.Div(
                [
                    html.P(label, className="atlas-label"),
                    html.H2(title, id=f"atlas-{page}-title"),
                    html.P(deck, className="atlas-deck"),
                ]
            )
        ]
        + ([side] if side is not None else []),
        className="atlas-lede",
    )


def table(
    headers: list[Any],
    rows: list[list[Any]],
    *,
    numeric: tuple[int, ...] = (),
    left: tuple[int, ...] = (),
    kind: str = "",
    label: str = "Data table",
    footer: list[Any] | None = None,
) -> Any:
    def cell_class(i: int) -> str:
        base = "atlas-num" if i in numeric else "atlas-left" if i in left else ""
        return base + (" atlas-desktop-outcomes" if kind == "outcome" and i > 1 else "")

    def row(values: list[Any]) -> Any:
        return html.Tr(
            [html.Th(values[0], scope="row")]
            + [
                html.Td(
                    v,
                    className=cell_class(i),
                    **cast(
                        dict[str, Any],
                        {"data-label": headers[i]}
                        if kind in {"site-gap", "flight", "launch"}
                        else {},
                    ),
                )
                for i, v in enumerate(values[1:], 1)
            ]
        )

    result = html.Table(
        [
            html.Thead(
                html.Tr(
                    [
                        html.Th(h, scope="col", className=cell_class(i))
                        for i, h in enumerate(headers)
                    ]
                )
            ),
            html.Tbody([row(values) for values in rows]),
        ]
        + ([html.Tfoot(row(footer))] if footer is not None else []),
        className="atlas-table" + (f" atlas-{kind}-table" if kind else ""),
        **cast(dict[str, Any], {"aria-label": label}),
    )
    return html.Div(
        result,
        className="atlas-scroll",
        role="region",
        **cast(dict[str, Any], {"aria-label": label}),
    )


def split_table(headers: list[Any], rows: list[list[Any]], first: int, noun: str, **kwargs: Any):
    children = [table(headers, rows[:first], **kwargs)]
    if len(rows) > first:
        children.append(
            html.Details(
                [
                    html.Summary(f"Show {len(rows) - first:,} more {noun}"),
                    table(headers, rows[first:], **kwargs),
                ],
                className="atlas-disclosure",
            )
        )
    return children


def bars(values: list[tuple[str, int, bool]], maximum: int | None = None) -> Any:
    scale = max(maximum or 0, *(v[1] for v in values), 1)
    return html.Div(
        [
            html.Div(
                [
                    html.Span(label),
                    html.I(style={"--v": f"{count / scale * 100}%"}),
                    html.B(number(count)),
                ],
                className="atlas-row",
                role="listitem",
                **cast(dict[str, Any], {"data-focus": "true"} if focus else {}),
            )
            for label, count, focus in values
        ],
        className="atlas-rows",
        role="list",
    )


def annual_figure(
    atlas: Atlas, since: int = 2000, start: int | None = None, end: int | None = None
) -> go.Figure:
    start, end = start or atlas.comparison_start, end or atlas.last_year
    years = list(range(since, atlas.last_year + 1))
    values = [atlas.annual[y] for y in years]
    top = max(350, math.ceil(max(values, default=0) / 50) * 50)
    fig = go.Figure(
        go.Bar(
            x=years,
            y=values,
            marker_color=["#223DA4" if y in {start, end} else "#818BA7" for y in years],
            marker_line_width=0,
            hovertemplate="%{x} · %{y:,} attempts<extra></extra>",
        )
    )
    ticks = (
        [1957, 1970, 1985, 2000, 2010, 2020, atlas.last_year]
        if since == 1957
        else (list(range(2000, atlas.last_year + 1, 5)) + [atlas.last_year])
    )
    fig.update_layout(
        template="none",
        height=215,
        margin=dict(l=36, r=8, t=22, b=25),
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family="IBM Plex Mono, monospace", size=11, color="#505761"),
        bargap=0.06 if since == 1957 else 0.1,
        xaxis=dict(
            tickmode="array",
            tickvals=sorted(set(ticks)),
            range=[since - 0.6, atlas.last_year + 0.6],
            fixedrange=True,
            showgrid=False,
            zeroline=False,
        ),
        yaxis=dict(
            range=[0, top],
            tickvals=[0, top / 2, top],
            gridcolor="#D9DEE6",
            zeroline=True,
            zerolinecolor="#D9DEE6",
            fixedrange=True,
        ),
        hoverlabel=dict(bgcolor="white", font_family="Instrument Sans", font_size=12),
        showlegend=False,
    )
    for year in {start, end}:
        if year in years:
            fig.add_annotation(
                x=year,
                y=atlas.annual[year],
                text=f"{year} · {atlas.annual[year]:,}",
                showarrow=False,
                yshift=13,
                xanchor="right",
                font=dict(color="#223DA4", size=11),
            )
    return fig


def overview_parts(atlas: Atlas, start: int, year: int, since: int = 2000) -> list[Any]:
    before, after = atlas.annual[start], atlas.annual[year]
    ratio = after / before if before else 0
    title = (
        ["Launch attempts", html.Br(), "nearly tripled"]
        if 2.75 <= ratio < 3
        else (["Launch attempts", html.Br(), f"{signed(after - before)} from {start} to {year}"])
    )
    difference = after - before
    spacex = atlas.provider_year[121, year] - atlas.provider_year[121, start]
    verb = "changed" if difference == 0 else "rose" if difference > 0 else "fell"
    deck = f"Recorded attempts {verb} from {before:,} in {start} to {after:,} in {year}. "
    deck += (
        f"SpaceX accounted for {spacex:,} of the {difference:,} additional attempts."
        if difference > 0 and 0 <= spacex <= difference
        else f"SpaceX's change was {signed(spacex)} attempts."
    )
    ids = sorted(
        {pid for pid, y in atlas.provider_year if y in {start, year}},
        key=lambda pid: (
            -abs(atlas.provider_year[pid, year] - atlas.provider_year[pid, start]),
            str(pid),
        ),
    )[:5]
    growth = [
        [
            atlas.providers.get(key, "Unknown provider"),
            number(atlas.provider_year[key, start]),
            number(atlas.provider_year[key, year]),
            signed(atlas.provider_year[key, year] - atlas.provider_year[key, start]),
        ]
        for key in ids
    ]
    other_before = before - sum(atlas.provider_year[key, start] for key in ids)
    other_after = after - sum(atlas.provider_year[key, year] for key in ids)
    growth.append(
        [
            "All other providers",
            number(other_before),
            number(other_after),
            signed(other_after - other_before),
        ]
    )
    cutoff = atlas.as_of.date()
    end_day = date_label(cutoff.fromordinal(cutoff.toordinal() - 1)).rsplit(" ", 1)[0]
    delta = atlas.ytd[atlas.as_of.year] - atlas.ytd[atlas.last_year]
    finding = (
        f"{abs(delta):,} {'more' if delta > 0 else 'fewer'} attempts than in {atlas.last_year}."
    )
    if delta == 0:
        finding = f"The same number of attempts as in {atlas.last_year}."
    return [
        lede(
            "overview",
            "Orbital launch activity",
            title,
            deck,
            html.Div(
                [html.B(number(after)), html.Span(f"recorded attempts in {year}")],
                className="atlas-lede-side",
            ),
        ),
        html.Figure(
            [
                heading(
                    "Orbital attempts by year",
                    "Complete UTC calendar years",
                    control(
                        "History",
                        "atlas-history",
                        [(2000, "Since 2000"), (1957, "Since 1957")],
                        2000,
                    ),
                ),
                dcc.Graph(
                    id="annual-chart",
                    figure=annual_figure(atlas, since, start, year),
                    responsive=True,
                    style={"height": "215px", "width": "100%"},
                    config={"displayModeBar": False, "responsive": True},
                ),
            ],
            className="atlas-figure",
        ),
        html.Div(
            [
                html.Section(
                    [
                        html.H3("Who drove the change?"),
                        html.P(
                            f"Recorded attempts by provider, {start} and {year}",
                            className="atlas-note",
                        ),
                        table(
                            ["Provider", str(start), str(year), "Change"],
                            growth,
                            numeric=(1, 2, 3),
                            footer=["Total", number(before), number(after), signed(difference)],
                            label="Provider contribution to the change in attempts",
                        ),
                    ]
                ),
                html.Section(
                    [
                        html.H3("So far this year"),
                        html.P(f"1 January to {end_day} UTC in each year", className="atlas-note"),
                        bars(
                            [
                                (str(y), atlas.ytd[y], y == atlas.as_of.year)
                                for y in range(atlas.as_of.year - 3, atlas.as_of.year + 1)
                            ],
                            300,
                        ),
                        html.P(finding, className="atlas-finding"),
                    ]
                ),
            ],
            className="atlas-pair",
        ),
    ]


def overview(atlas: Atlas) -> list[Any]:
    parts = overview_parts(atlas, atlas.comparison_start, atlas.last_year)
    options = [(year, str(year)) for year in atlas.comparison_years]
    return [
        html.Div(
            [
                control("Compare year", "atlas-compare-start", options, atlas.comparison_start),
                control("With year", "atlas-compare-end", options, atlas.last_year),
                html.Span(
                    "Complete UTC calendar years · current-year comparison below uses YTD",
                    className="atlas-note",
                ),
            ],
            className="atlas-filter-bar",
        ),
        html.Div(parts[0], id="atlas-overview-story"),
        parts[1],
        html.Div(parts[2], id="atlas-comparison"),
    ]


def context_details(atlas: Atlas, group: str, key: Any) -> Any:
    children = []
    for label, values in atlas.entity_context(group, key):
        text = ", ".join(values) or "Unknown"
        if len(values) > 3:
            children.append(html.Details([html.Summary(f"{label}: {len(values):,}"), html.P(text)]))
        else:
            children.append(html.Div(f"{label}: {text}"))
    return html.Div(children, className="atlas-entity-context")


def page_slice(total: int, page: int, size: int) -> tuple[int, int, int, str]:
    size = size or max(total, 1)
    page = max(0, min(page, max(0, (total - 1) // size)))
    first, last = page * size, min((page + 1) * size, total)
    return page, first, last, f"{first + 1 if total else 0:,}–{last:,} of {total:,} rows"


def pagination(prefix: str, *, allow_all: bool = False) -> Any:
    return html.Div(
        [
            control(
                "Rows",
                prefix + "-size",
                [(25, "25"), (50, "50"), (100, "100")] + ([(0, "All")] if allow_all else []),
                25,
            ),
            html.Button("Previous", id=prefix + "-prev", disabled=True, type="button"),
            html.Span(id=prefix + "-page-label", role="status"),
            html.Button("Next", id=prefix + "-next", type="button"),
        ],
        className="atlas-pagination",
    )


def outcome_rows(atlas: Atlas, group: str, search: str = "") -> list[dict[str, Any]]:
    term = search.strip().casefold()
    return [
        row
        for row in atlas.outcomes(group)
        if not term
        or term
        in (
            row["name"]
            + " "
            + " ".join(
                name for _, names in atlas.entity_context(group, row["id"]) for name in names
            )
        ).casefold()
    ]


def outcomes_table(
    atlas: Atlas, group: str, page: int = 0, size: int = 25, search: str = ""
) -> list[Any]:
    results = outcome_rows(atlas, group, search)
    _, first, last, _ = page_slice(len(results), page, size)
    rows = []
    for row in results[first:last]:
        ratio = rate(row["success_count"], row["classified_outcome_count"])
        breakdown = html.Details(
            [
                html.Summary("Outcome breakdown"),
                html.Dl(
                    [
                        item
                        for label, value in (
                            ("Success rate", ratio),
                            ("Failures", number(row["failure_count"])),
                            ("Partial failures", number(row["partial_failure_count"])),
                            ("Unresolved", number(row["unresolved_count"])),
                        )
                        for item in (html.Dt(label), html.Dd(value))
                    ]
                ),
            ],
            className="atlas-mobile-outcomes atlas-disclosure",
        )
        rows.append(
            [
                html.Div(
                    [html.Span(row["name"]), context_details(atlas, group, row["id"]), breakdown],
                    **cast(dict[str, Any], {"data-entity-id": str(row["id"])}),
                ),
                f"{row['success_count']:,} / {row['classified_outcome_count']:,}",
                ratio,
                number(row["failure_count"]),
                number(row["partial_failure_count"]),
                number(row["unresolved_count"]),
            ]
        )
    title = {"configuration": "Configuration", "family": "Vehicle family", "provider": "Provider"}[
        group
    ]
    return [
        table(
            [title, "Successes / classified", "Success rate", "Failure", "Partial", "Unresolved"],
            rows,
            kind="outcome",
            numeric=(1, 2, 3, 4, 5),
            label=f"Launch outcomes by {title.lower()}",
        )
    ]


def vehicles(atlas: Atlas) -> list[Any]:
    outcomes = atlas.outcomes("configuration")
    examples = {r["name"].replace(" ", "").lower(): r for r in outcomes}
    sample = [examples[k] for k in ("falcon9block5", "soyuz2.1a") if k in examples]
    if len(sample) != 2:
        sample = outcomes[:2]
    deck = " ".join(
        f"{r['name']} recorded {r['success_count']:,} successes in "
        f"{r['classified_outcome_count']:,} classified attempts."
        for r in sample
    )
    return [
        lede("vehicles", "Providers & vehicles", "The sample size changes the story", deck),
        html.Section(
            [
                heading(
                    "Launch outcomes",
                    "Since 2000 · ranked by attempt count",
                    control(
                        "Group by",
                        "atlas-reliability",
                        [
                            (k, v)
                            for k, v in (
                                ("configuration", "Configuration"),
                                ("family", "Vehicle family"),
                                ("provider", "Provider"),
                            )
                        ],
                        "configuration",
                    ),
                    "atlas-vehicle-note",
                ),
                html.Div(
                    [
                        html.Label("Search names or relationships", htmlFor="atlas-outcome-search"),
                        dcc.Input(
                            id="atlas-outcome-search",
                            type="search",
                            value="",
                            debounce=True,
                            placeholder="Configuration, family or provider…",
                        ),
                    ],
                    className="atlas-search",
                ),
                html.P(
                    "Families can overlap; providers are those observed on launches since 2000.",
                    className="atlas-note",
                ),
                pagination("atlas-outcome", allow_all=True),
                html.Div(
                    outcomes_table(atlas, "configuration"),
                    id="atlas-outcomes",
                    **cast(dict[str, Any], {"aria-live": "polite"}),
                ),
            ],
            className="atlas-figure",
        ),
    ]


def site_map(
    atlas: Atlas, selected: str, focus: str = "world", revision: str = "initial"
) -> go.Figure:
    sites = atlas.mapped_sites
    maximum = max((s["attempts"] for s in sites), default=1)
    chosen = next((s for s in sites if str(s["id"]) == selected), None)
    center, zoom = {"lat": 0, "lon": 0}, 1
    if focus == "site" and chosen:
        center, zoom = {"lat": chosen["latitude"], "lon": chosen["longitude"]}, 10
    elif focus == "florida":
        nearby = [site for site in sites if site["id"] in {12, 27}]
        center = {
            "lat": sum(site["latitude"] for site in nearby) / len(nearby) if nearby else 28.51,
            "lon": sum(site["longitude"] for site in nearby) / len(nearby) if nearby else -80.61,
        }
        zoom = 10
    fig = go.Figure(
        go.Scattermap(
            lat=[s["latitude"] for s in sites],
            lon=[s["longitude"] for s in sites],
            customdata=[str(s["id"]) for s in sites],
            text=[s["name"].split(",")[0] for s in sites],
            mode="markers+text" if focus != "world" else "markers",
            textposition="top center",
            textfont={"size": 11, "color": "#202329"},
            marker={
                "symbol": "circle",
                "size": [8 + 10 * math.sqrt(s["attempts"] / maximum) for s in sites],
                "color": ["#223DA4" if str(s["id"]) == selected else "#8192CE" for s in sites],
                "opacity": 0.9,
            },
            hovertext=[f"{s['name']} · {s['attempts']:,} attempts" for s in sites],
            hovertemplate="%{hovertext}<extra></extra>",
        )
    )
    fig.update_layout(
        map={"style": "carto-positron", "center": center, "zoom": zoom},
        height=390,
        meta={"atlas_map_view": focus, "atlas_map_revision": revision},
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        showlegend=False,
        paper_bgcolor="#F5F6F8",
        font={"family": "Instrument Sans", "color": "#202329"},
    )
    return fig


def site_readout(atlas: Atlas, selected: str) -> str:
    site = next((s for s in atlas.mapped_sites if str(s["id"]) == selected), None)
    if site is None:
        return "No mapped sites in this edition."
    gap = (
        f"{site['median']:.1f} days median observed gap ({len(site['gaps']):,} supported gaps)"
        if site["median"] is not None
        else "No supported gaps"
    )
    return f"{site['name'].split(',')[0]} · {site['attempts']:,} attempts · {gap}"


def spaceports(atlas: Atlas) -> list[Any]:
    sites, months = atlas.featured_sites, atlas.months
    selected = str(sites[0]["id"]) if sites else ""
    deck = (
        "Since January 2024, "
        + "; ".join(
            f"{s['name'].split(',')[0]} recorded a median observed gap of {s['median']:.1f} days"
            for s in sites[:2]
            if s["median"] is not None
        )
        + "."
    )
    top_gaps = [s["median"] for s in sites[:2] if s["median"] is not None]
    top_gap = sum(top_gaps) / len(top_gaps) if top_gaps else None
    title = (
        f"About {round(top_gap):,} days between attempts at the two busiest sites"
        if top_gap is not None
        else ("Explore recorded activity at the busiest sites")
    )
    maximum = max((s["monthly"][m] for s in sites for m in months), default=0)
    heat = []
    years = [
        html.Span(str(m.year), style={"gridColumn": f"{i + 1} / span 6"})
        for i, m in enumerate(months)
        if m.month == 1
    ]
    heat.append(
        html.Div(
            [html.Span(), html.Div(years, className="atlas-cadence-months")],
            className="atlas-cadence-row atlas-cadence-year",
        )
    )
    for site in sites:
        heat.append(
            html.Div(
                [
                    html.Span(site["name"].split(",")[0]),
                    html.Div(
                        [
                            html.I(
                                style={"--level": site["monthly"][m] / max(maximum, 1)},
                                title=f"{site['name']} · {m:%b %Y} · {site['monthly'][m]} attempts",
                            )
                            for m in months
                        ],
                        className="atlas-cadence-months",
                    ),
                ],
                className="atlas-cadence-row",
            )
        )
    month_rows = [[f"{m:%b %Y}", *[number(s["monthly"][m]) for s in sites]] for m in months]
    gaps = [
        [
            s["name"],
            number(s["attempts"]),
            "—" if s["median"] is None else f"{s['median']:.1f}",
            number(len(s["gaps"])),
        ]
        for s in atlas.sites
    ]
    return [
        lede("sites", "Spaceports", title, deck),
        html.Section(
            [
                heading(
                    "Spaceports with recorded activity",
                    f"All {len(atlas.mapped_sites):,} sites with coordinates and recorded "
                    "orbital attempts since January 2024",
                    control(
                        "Site",
                        "atlas-site-select",
                        [(s["id"], s["name"].split(",")[0]) for s in atlas.mapped_sites],
                        selected,
                    ),
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Button("World view", id="atlas-map-world", type="button"),
                                html.Button(
                                    "Florida close-up", id="atlas-map-florida", type="button"
                                ),
                            ],
                            className="atlas-map-actions",
                        ),
                        dcc.Graph(
                            id="atlas-map-graph",
                            figure=site_map(atlas, selected),
                            responsive=True,
                            style={"height": "390px", "width": "100%"},
                            config={
                                "scrollZoom": True,
                                "displayModeBar": True,
                                "displaylogo": False,
                                "modeBarButtons": [["zoomInMap", "zoomOutMap"]],
                            },
                        ),
                    ],
                    id="atlas-map-plot",
                ),
                html.P(
                    site_readout(atlas, selected),
                    id="atlas-map-readout",
                    className="atlas-finding",
                    **cast(dict[str, Any], {"aria-live": "polite"}),
                ),
                html.P(
                    "Drag to pan; use + / − or scroll to zoom. Choose a site to zoom in. "
                    "Nearby Florida sites overlap at world scale; the close-up separates them.",
                    className="atlas-note",
                ),
            ],
            className="atlas-figure",
        ),
        html.Figure(
            [
                heading(
                    "Monthly activity at the busiest sites",
                    f"January 2024 to {date_label(atlas.as_of)} UTC · {atlas.as_of:%B} is partial",
                ),
                html.Div(heat, id="atlas-cadence", style={"--months": len(months)}),
                html.P(
                    [
                        html.Span("0"),
                        *[html.I(style={"--level": value}) for value in (0, 0.25, 0.5, 0.75, 1)],
                        html.Span(str(maximum)),
                        html.Span("attempts per month"),
                    ],
                    className="atlas-scale",
                ),
                html.Details(
                    [
                        html.Summary("Monthly counts"),
                        table(
                            ["Month", *[s["name"].split(",")[0] for s in sites]],
                            month_rows,
                            numeric=tuple(range(1, len(sites) + 1)),
                            label="Exact monthly launch counts",
                        ),
                    ],
                    className="atlas-disclosure",
                ),
            ],
            className="atlas-rule atlas-cadence-panel",
        ),
        html.Section(
            [
                html.H3("Time between observed attempts"),
                html.P(
                    (
                        "Gaps end in the period shown and require supported "
                        "precision on both launches."
                    ),
                    className="atlas-note",
                ),
                *split_table(
                    ["Site", "Attempts", "Median days", "Supported gaps"],
                    gaps,
                    8,
                    "sites",
                    numeric=(1, 2, 3),
                    kind="site-gap",
                    label="Observed launch gaps by site",
                ),
            ],
            className="atlas-rule",
        ),
    ]


def booster_history(atlas: Atlas, selected: str) -> tuple[str, Any, Any]:
    key = next((key for key in atlas.boosters if str(key) == selected), None)
    if key is None:
        return (
            "No recorded flights",
            html.Div(),
            html.P("No identifiable booster flights in this edition."),
        )
    rows = atlas.boosters[key]
    serial = rows[0]["serial_number"] or f"Hardware {key}"
    start, end = utc(rows[0]["launch_net_utc"]).date(), utc(rows[-1]["launch_net_utc"]).date()
    days = max((end - start).days, 1)
    marks = []
    for r in rows:
        stamp = utc(r["launch_net_utc"])
        turnaround = (
            f" · {r['turnaround_days']:.1f} days since previous supported flight"
            if r["turnaround_days"] is not None
            else ""
        )
        marks.append(
            html.I(
                style={"--x": f"{100 * (stamp.date() - start).days / days}%", "--height": "25px"},
                className="atlas-flight-mark",
                tabIndex=0,
                title=(
                    f"Flight {number(r['source_flight_number'])} "
                    f"· {date_label(stamp)} · {atlas.launch_name(r['launch_id'])}{turnaround}"
                ),
            )
        )
    ticks = [
        html.Span(str(year), style={"--x": f"{100 * (date(year, 1, 1) - start).days / days}%"})
        for year in range(start.year + 1, end.year + 1)
    ]
    strip = html.Div(
        [
            html.Div(marks, className="atlas-flight-track"),
            html.Div(ticks, className="atlas-flight-ticks"),
        ],
        role="img",
        **cast(
            dict[str, Any],
            {
                "aria-label": (
                    f"{serial}: one tick per recorded flight, {date_label(start)} "
                    f"to {date_label(end)}"
                )
            },
        ),
    )
    log = [
        [
            atlas.launch_name(r["launch_id"]),
            date_label(r["launch_net_utc"]),
            number(r["source_flight_number"]),
            "—" if r["turnaround_days"] is None else f"{r['turnaround_days']:.1f}",
        ]
        for r in reversed(rows)
    ]
    subtitle = (
        f"{len(rows):,} recorded flights · {sum(r['turnaround_days'] is not None for r in rows):,} "
        f"supported turnaround intervals"
    )
    return (
        subtitle,
        strip,
        split_table(
            ["Launch", "UTC date", "Flight", "Turnaround (days)"],
            log,
            7,
            "earlier flights",
            numeric=(2, 3),
            kind="flight",
            label=f"Recorded flights for {serial}",
        ),
    )


def booster_details(atlas: Atlas, selected: str) -> Any:
    return html.Dl(
        [
            html.Div([html.Dt(label), html.Dd(", ".join(values) or "Unknown")])
            for label, values in atlas.booster_context(selected)
        ],
        className="atlas-booster-context",
    )


def reuse(atlas: Atlas) -> list[Any]:
    selected = str(atlas.booster_options[0]) if atlas.booster_options else ""
    subtitle, strip, log = booster_history(atlas, selected)
    landing = atlas.tables["mart_landing"]
    successful = sum(r["successful_landing_count"] for r in landing)
    failed = sum(r["known_landing_outcome_count"] for r in landing) - successful
    unknown = sum(r["unknown_landing_outcome_count"] for r in landing)
    total = sum(r["attempted_landing_count"] for r in landing)
    return [
        lede(
            "reuse",
            "Booster reuse",
            "Follow a booster across its flights",
            (
                "Explore when a booster flew and how much time passed between supported "
                "consecutive flights. Its recorded provider, vehicle configurations and family "
                "memberships appear with the selected serial number."
            ),
        ),
        html.Section(
            [
                heading(
                    "Recorded flight history",
                    subtitle,
                    control(
                        "Serial number",
                        "atlas-booster",
                        [
                            (key, atlas.boosters[key][0]["serial_number"] or f"Hardware {key}")
                            for key in atlas.booster_options
                        ],
                        selected,
                    ),
                    "atlas-booster-note",
                ),
                html.Div(booster_details(atlas, selected), id="atlas-booster-context"),
                html.Div(strip, id="atlas-booster-timeline"),
                html.Div(log, id="atlas-booster-history"),
            ],
            className="atlas-figure",
        ),
        html.Div(
            [
                html.Section(
                    [
                        html.H3("Fleet landing outcomes"),
                        html.P(
                            "Separate landing attempts across all recorded hardware",
                            className="atlas-note",
                        ),
                        table(
                            ["Outcome", "Landings"],
                            [
                                ["Successful", number(successful)],
                                ["Failed", number(failed)],
                                ["Unknown outcome", number(unknown)],
                            ],
                            numeric=(1,),
                            footer=["Landing attempts", number(total)],
                            label="Recorded landing outcomes",
                        ),
                    ]
                ),
                html.Section(
                    [
                        html.H3("Hardware coverage"),
                        html.P(
                            f"{atlas.reused:,} of {atlas.reuse_known:,} flights "
                            "with known reuse status were reused. "
                            f"SpaceX accounts for {atlas.spacex_flights:,} of the "
                            f"{atlas.identifiable:,} identifiable hardware flights. In all, "
                            f"{atlas.coverage['attempts_with_recorded_stage_flights']:,} "
                            f"of {atlas.coverage['orbital_attempt_records']:,} orbital attempts "
                            "have an associated stage-flight record.",
                            className="atlas-deck",
                        ),
                    ]
                ),
            ],
            className="atlas-pair",
        ),
    ]


def manifest_axis(atlas: Atlas, row: bool = False) -> Any:
    start, end = atlas.horizon
    return html.Span(
        [html.Span(f"{d.day} {d:%b}") for d in (start + (end - start) * (i / 4) for i in range(5))],
        className="atlas-time-axis" + (" atlas-row-axis" if row else ""),
        **cast(dict[str, Any], {"aria-hidden": "true"}),
    )


def manifest(atlas: Atlas, precision: str = "") -> tuple[str, list[Any]]:
    rows = [r for r in atlas.upcoming if not precision or r["interval_basis"] == precision]
    display = []
    for row in rows:
        left, width = window_position(row)
        bounds = (
            f"{row['schedule_earliest_utc']} to {row['schedule_latest_exclusive_utc']} "
            f"· end exclusive"
        )
        attrs = {"data-exact": "true"} if row["interval_basis"] in ("second", "minute") else {}
        status = [row["source_status_name"]]
        if row["scope_classification"] == "unknown":
            status.append(html.Div("Orbital scope unknown", className="atlas-note"))
        display.append(
            [
                html.Span(
                    atlas.launch_name(row["launch_id"]),
                    **cast(
                        dict[str, Any],
                        {
                            "data-window-start": row["schedule_earliest_utc"],
                            "data-launch-id": row["launch_id"],
                        },
                    ),
                ),
                atlas.providers.get(atlas.labels.get(row["launch_id"], {}).get("provider_id"))
                or "Unknown provider",
                html.Div(status),
                html.Div(
                    [
                        html.Span(window_label(row)),
                        html.Span(
                            html.I(
                                style={"--left": f"{left}%", "--v": f"{width}%"},
                                **cast(dict[str, Any], attrs),
                            ),
                            className="atlas-time-track",
                            role="img",
                            title=bounds,
                            **cast(dict[str, Any], {"aria-label": bounds}),
                        ),
                        manifest_axis(atlas, True),
                    ],
                    className="atlas-manifest-window",
                ),
            ]
        )
    headers = [
        "Launch",
        "Provider",
        "Source status",
        html.Div(["Reported window", manifest_axis(atlas)]),
    ]
    result = split_table(
        headers,
        display,
        7,
        "entries",
        kind="manifest",
        left=(1, 2, 3),
        label="Launch manifest with reported UTC windows",
    )
    if not rows:
        result.append(html.P("No entries match this precision.", className="atlas-note"))
    return (
        (
            f"{len(rows):,} {'entry overlaps' if len(rows) == 1 else 'entries overlap'} "
            f"the frozen 90-day horizon · dates in UTC"
        ),
        result,
    )


def planned_visual(
    atlas: Atlas, group: str = "provider", provider: str = "", precision: str = ""
) -> tuple[str, str, Any]:
    rows, total = atlas.planned_counts(group, provider, precision)
    title = (
        "Planned launches by "
        + {"provider": "provider", "configuration": "configuration", "family": "vehicle family"}[
            group
        ]
    )
    note = f"{total:,} manifest entries · {PRECISIONS.get(precision, 'All precisions')}"
    if provider:
        note += " · " + next(
            (
                atlas.providers.get(key) or "Unknown provider"
                for key in {
                    atlas.labels[row["launch_id"]].get("provider_id") for row in atlas.upcoming
                }
                if str(key) == provider
            ),
            "Unknown provider",
        )
    if group == "family":
        note += ". Family memberships overlap; do not sum the family bars."
    scale = max((row["count"] for row in rows), default=1)
    chart = html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [html.Span(row["name"]), html.B(number(row["count"]))],
                        className="atlas-planned-label",
                    ),
                    html.Div(
                        html.I(style={"width": f"{100 * row['count'] / scale}%"}),
                        className="atlas-planned-track",
                    ),
                ],
                className="atlas-planned-row",
                role="listitem",
                **cast(
                    dict[str, Any],
                    {
                        "data-group-id": str(row["id"]),
                        "data-count": str(row["count"]),
                    },
                ),
            )
            for row in rows
        ],
        className="atlas-planned-bars",
        role="list",
        **cast(dict[str, Any], {"aria-label": title}),
    )
    if not rows:
        chart = html.P("No manifest entries match this selection.", className="atlas-note")
    return title, note, chart


def schedule(atlas: Atlas) -> list[Any]:
    subtitle, rows = manifest(atlas)
    year_count = atlas.precision_counts["year"]
    total = len(atlas.upcoming)
    if total and year_count > total / 2:
        title = "Most entries have only a launch year"
    else:
        title = "Explore the precision of upcoming launch windows"
    start, end = atlas.horizon
    deck = (
        f"{year_count:,} of the {total:,} entries overlapping the 90-day window of "
        f"{date_label(start)} to {date_label(end)} have year-level precision."
    )
    coverage = atlas.coverage
    facts = [
        ("Source records", coverage["current_launch_records"]),
        ("Recorded orbital attempts", coverage["orbital_attempt_records"]),
        ("Source records with unknown orbital scope", coverage["unknown_scope_records"]),
        (
            "Manifest entries with unknown orbital scope",
            sum(r["scope_classification"] == "unknown" for r in atlas.upcoming),
        ),
        (
            "Eligible-status records without supported date precision",
            coverage["unsupported_upcoming_precision_records"],
        ),
    ]
    unsupported = coverage["unsupported_upcoming_precision_records"]
    chart_title, chart_note, chart = planned_visual(atlas)
    provider_ids = {atlas.labels[row["launch_id"]].get("provider_id") for row in atlas.upcoming}
    return [
        lede("schedule", "Upcoming launch windows", title, deck),
        html.Section(
            [
                html.H3(chart_title, id="atlas-planned-title"),
                html.P(
                    "Counts cover reported windows overlapping the frozen 90-day horizon, "
                    "including broad year/quarter windows. They are not confirmed launch dates.",
                    className="atlas-note",
                ),
                html.Div(
                    [
                        control(
                            "View",
                            "atlas-planned-group",
                            [
                                ("provider", "By provider"),
                                ("configuration", "By configuration"),
                                ("family", "By vehicle family"),
                            ],
                            "provider",
                        ),
                        control(
                            "Provider",
                            "atlas-planned-provider",
                            [("", "All providers")]
                            + sorted(
                                [
                                    (str(key), atlas.providers.get(key) or "Unknown provider")
                                    for key in provider_ids
                                ],
                                key=lambda item: item[1],
                            ),
                            "",
                        ),
                    ],
                    className="atlas-filter-bar atlas-planned-controls",
                ),
                html.P(chart_note, id="atlas-planned-note", className="atlas-note", role="status"),
                html.Div(chart, id="atlas-planned-chart"),
            ],
            className="atlas-figure atlas-planned-panel",
        ),
        html.Section(
            [
                heading(
                    "Launch manifest",
                    subtitle,
                    control(
                        "Launch Precision",
                        "atlas-precision-select",
                        [
                            ("", "All precisions"),
                            *[(k, v) for k, v in PRECISIONS.items() if atlas.precision_counts[k]],
                        ],
                        "",
                    ),
                    "atlas-manifest-count",
                ),
                html.P(
                    (
                        "Shaded intervals show possible dates within "
                        "the report horizon. Ticks mark entries timed "
                        "to the minute or second. Status appears beside "
                        "each launch. Sorted by reported window start, earliest first; "
                        "ties use window end then launch ID. Coarse dates retain "
                        "their original precision."
                    ),
                    className="atlas-note",
                ),
                html.Div(rows, id="atlas-manifest"),
            ],
            className="atlas-figure",
        ),
        html.Details(
            [
                html.Summary("Coverage and methodology"),
                html.Dl(
                    [html.Div([html.Dt(label), html.Dd(number(value))]) for label, value in facts]
                ),
                html.P(
                    (
                        f"The {unsupported:,} records lack a supported "
                        f"schedule interval. Their overlap with the horizon "
                        f"is unknown. Launch Library 2 is a community-maintained "
                        f"source."
                    ),
                    className="atlas-note",
                ),
                html.P(
                    "History extraction complete: "
                    f"{'Yes' if coverage['history_complete'] else 'No'}. "
                    "Last completed source refresh: "
                    f"{coverage['last_complete_refresh_utc'] or 'Unknown'}.",
                    className="atlas-note",
                ),
            ],
            className="atlas-rule atlas-method",
        ),
    ]


def workspace(atlas: Atlas) -> list[Any]:
    edition = date_label(atlas.as_of)
    contents = {
        "overview": overview,
        "vehicles": vehicles,
        "sites": spaceports,
        "reuse": reuse,
        "schedule": schedule,
    }
    return [
        html.Header(
            [
                html.Div(
                    [
                        html.Span(**cast(dict[str, Any], {"aria-hidden": "true"})),
                        "Space Launch Atlas",
                    ],
                    className="atlas-wordmark",
                ),
                html.Div(f"Data as of {edition} · UTC", className="atlas-edition"),
            ],
            className="atlas-mast",
        ),
        html.Nav(
            [
                html.Button(
                    label,
                    id="atlas-nav-" + key,
                    type="button",
                    disable_n_clicks=True,
                    **cast(
                        dict[str, Any],
                        {
                            "data-page": key,
                            **({"aria-current": "page"} if key == "overview" else {}),
                        },
                    ),
                )
                for key, label in PAGES.items()
            ],
            className="atlas-nav",
            **cast(dict[str, Any], {"aria-label": "Report pages"}),
        ),
        html.Main(
            [
                html.Section(
                    render(atlas),
                    id="atlas-page-" + key,
                    hidden=key != "overview",
                    **cast(
                        dict[str, Any],
                        {"data-chapter": key, "aria-labelledby": f"atlas-{key}-title"},
                    ),
                )
                for key, render in contents.items()
            ],
            className="atlas-main",
        ),
        html.Footer(
            [html.Span("Source · Launch Library 2"), html.Span(f"Production data cut · {edition}")],
            className="atlas-footer",
        ),
        html.Details(
            [
                html.Summary("Edition details"),
                html.Dl(
                    [
                        html.Dt("Reporting release"),
                        html.Dd(atlas.edition.release_id),
                        html.Dt("Source snapshot"),
                        html.Dd(atlas.edition.snapshot_id),
                        html.Dt("Analytical cut (UTC)"),
                        html.Dd(atlas.edition.analysis_as_of),
                        html.Dt("Release activated (UTC)"),
                        html.Dd(atlas.edition.activated_at),
                    ]
                ),
            ],
            className="atlas-edition-details",
        ),
    ]
