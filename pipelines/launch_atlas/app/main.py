"""Dash application factory and callbacks; all data access is deferred to page load."""

from __future__ import annotations

import logging
from typing import Any

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

from launch_atlas.app import views
from launch_atlas.app.data import EditionReader
from launch_atlas.app.model import Atlas

LOGGER = logging.getLogger(__name__)
DEFAULTS = {
    "atlas-history": "2000",
    "atlas-reliability": "configuration",
    "atlas-site-select": "",
    "atlas-booster": "",
    "atlas-precision-select": "",
    "atlas-compare-start": "2020",
    "atlas-compare-end": "2025",
    "atlas-outcome-size": "25",
    "atlas-planned-group": "provider",
    "atlas-planned-provider": "",
}


def create_app(reader: EditionReader | None = None) -> Dash:
    reader = reader or EditionReader()
    app = Dash(
        __name__,
        assets_folder=str(views.ASSETS),
        title="Space Launch Atlas",
        update_title="",
        suppress_callback_exceptions=True,
    )
    models: dict[str, Atlas] = {}
    app.layout = html.Div(
        [
            dcc.Store(id="atlas-edition"),
            *[dcc.Store(id=key + "-value", data=value) for key, value in DEFAULTS.items()],
            dcc.Store(id="atlas-outcome-page", data=0),
            dcc.Interval(id="atlas-startup", interval=200, max_intervals=1),
            html.Div(
                "Connecting to the latest validated BigQuery release…",
                id="atlas-status",
                role="status",
                className="atlas-connection-status",
            ),
            html.Div(id="atlas-workspace"),
            html.Div(
                html.Button("Reload latest release", id="atlas-reload", type="button"),
                className="atlas-reload-panel",
            ),
        ],
        id="printed-launch-atlas",
    )

    @app.callback(
        Output("atlas-workspace", "children"),
        Output("atlas-edition", "data"),
        Output("atlas-status", "children"),
        *[Output(key + "-value", "data") for key in DEFAULTS],
        Input("atlas-startup", "n_intervals"),
        Input("atlas-reload", "n_clicks"),
        prevent_initial_call=True,
    )
    def load_edition(_intervals: int, _clicks: int | None) -> tuple[Any, ...]:
        try:
            edition = reader.latest()
            model = models.setdefault(edition.release_id, Atlas(edition))
            children = html.Div(views.workspace(model), key=f"{edition.release_id}-{_clicks or 0}")
            values = {
                **DEFAULTS,
                "atlas-booster": str(model.booster_options[0]) if model.booster_options else "",
                "atlas-compare-start": str(model.comparison_start),
                "atlas-compare-end": str(model.last_year),
                "atlas-site-select": str(model.mapped_sites[0]["id"]) if model.mapped_sites else "",
            }
            return (
                children,
                edition.release_id,
                "",
                *values.values(),
            )
        except Exception as exc:
            # Exceptions from auth libraries may include request details. Never expose or log them.
            LOGGER.error("Edition load failed (%s)", type(exc).__name__)
            message = (
                "The latest BigQuery edition could not be loaded. "
                "Check the local Google login, reader permissions and connection, "
                "then reload. Any previous edition stays visible."
            )
            return no_update, no_update, message, *([no_update] * len(DEFAULTS))

    def model_for(release_id: str) -> Atlas:
        # Browser-supplied edition IDs can only address already validated, server-held editions.
        return models[release_id]

    @app.callback(
        Output("annual-chart", "figure"),
        Output("atlas-overview-story", "children"),
        Output("atlas-comparison", "children"),
        Input("atlas-history-value", "data"),
        Input("atlas-compare-start-value", "data"),
        Input("atlas-compare-end-value", "data"),
        State("atlas-edition", "data"),
        prevent_initial_call=True,
    )
    def change_history(value: str, start: str, end: str, release_id: str) -> Any:
        if value not in {"1957", "2000"} or not release_id:
            return (no_update,) * 3
        model = model_for(release_id)
        valid = {str(year) for year in model.comparison_years}
        if start not in valid or end not in valid:
            return (no_update,) * 3
        parts = views.overview_parts(model, int(start), int(end), int(value))
        return views.annual_figure(model, int(value), int(start), int(end)), parts[0], parts[2]

    @app.callback(
        Output("atlas-outcomes", "children"),
        Output("atlas-vehicle-note", "children"),
        Output("atlas-outcome-page-label", "children"),
        Output("atlas-outcome-page", "data"),
        Output("atlas-outcome-prev", "disabled"),
        Output("atlas-outcome-next", "disabled"),
        Input("atlas-reliability-value", "data"),
        Input("atlas-outcome-size-value", "data"),
        Input("atlas-outcome-search", "value"),
        Input("atlas-outcome-prev", "n_clicks"),
        Input("atlas-outcome-next", "n_clicks"),
        Input("atlas-edition", "data"),
        State("atlas-outcome-page", "data"),
        prevent_initial_call=True,
    )
    def change_group(
        value: str, size: str, search: str, prev: int, nxt: int, release_id: str, page: int
    ) -> Any:
        if (
            value not in {"configuration", "provider", "family"}
            or not release_id
            or size not in {"0", "25", "50", "100"}
        ):
            return (no_update,) * 6
        page = turn_page("atlas-outcome", page, prev, nxt)
        model = model_for(release_id)
        count = len(views.outcome_rows(model, value, search or ""))
        page, _, last, label = views.page_slice(count, page, int(size))
        note = "Since 2000 · " + (
            "family membership can overlap" if value == "family" else "ranked by attempt count"
        )
        return (
            views.outcomes_table(model, value, page, int(size), search or ""),
            note,
            label,
            page,
            page == 0,
            last >= count,
        )

    def turn_page(prefix: str, page: int, prev: int, nxt: int) -> int:
        trigger = ctx.triggered_id
        if trigger == prefix + "-prev" and prev:
            return max(0, (page or 0) - 1)
        if trigger == prefix + "-next" and nxt:
            return (page or 0) + 1
        return 0

    @app.callback(
        Output("atlas-map-graph", "figure"),
        Output("atlas-map-readout", "children"),
        Input("atlas-site-select-value", "data"),
        Input("atlas-map-world", "n_clicks"),
        Input("atlas-map-florida", "n_clicks"),
        Input("atlas-edition", "data"),
        prevent_initial_call=True,
    )
    def change_site(value: str, _world: int, _florida: int, release_id: str) -> Any:
        if not release_id:
            return no_update, no_update
        model = model_for(release_id)
        if value not in {str(s["id"]) for s in model.mapped_sites}:
            return no_update, no_update
        trigger = ctx.triggered_id
        focus = (
            "florida"
            if trigger == "atlas-map-florida"
            else "site"
            if trigger == "atlas-site-select-value"
            else "world"
        )
        if "atlas-edition.data" in ctx.triggered_prop_ids:
            focus = "world"
        revision = f"{release_id}:{value}:{_world or 0}:{_florida or 0}"
        return views.site_map(model, value, focus, revision), views.site_readout(model, value)

    app.clientside_callback(
        """function(click) {
          if (!click || !click.points || !click.points.length) {
            return window.dash_clientside.no_update;
          }
          const value = String(click.points[0].customdata);
          const select = document.getElementById('atlas-site-select');
          if (select) select.value = value;
          return value;
        }""",
        Output("atlas-site-select-value", "data", allow_duplicate=True),
        Input("atlas-map-graph", "clickData"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("atlas-booster-note", "children"),
        Output("atlas-booster-timeline", "children"),
        Output("atlas-booster-history", "children"),
        Output("atlas-booster-context", "children"),
        Input("atlas-booster-value", "data"),
        Input("atlas-edition", "data"),
        prevent_initial_call=True,
    )
    def change_booster(value: str, release_id: str) -> Any:
        if not release_id or value not in {str(key) for key in model_for(release_id).boosters}:
            return (no_update,) * 4
        model = model_for(release_id)
        note, strip, history = views.booster_history(model, value)
        return note, strip, history, views.booster_details(model, value)

    @app.callback(
        Output("atlas-manifest-count", "children"),
        Output("atlas-manifest", "children"),
        Output("atlas-planned-title", "children"),
        Output("atlas-planned-note", "children"),
        Output("atlas-planned-chart", "children"),
        Input("atlas-precision-select-value", "data"),
        Input("atlas-planned-group-value", "data"),
        Input("atlas-planned-provider-value", "data"),
        Input("atlas-edition", "data"),
        prevent_initial_call=True,
    )
    def change_precision(value: str, group: str, provider: str, release_id: str) -> Any:
        if (
            value not in {"", *views.PRECISIONS}
            or group not in {"provider", "configuration", "family"}
            or not release_id
        ):
            return (no_update,) * 5
        model = model_for(release_id)
        provider_ids = {
            str(model.labels[row["launch_id"]].get("provider_id")) for row in model.upcoming
        }
        if provider and provider not in provider_ids:
            return (no_update,) * 5
        title, note, chart = views.planned_visual(model, group, provider, value)
        count, manifest = views.manifest(model, value)
        return count, manifest, title, note, chart

    @app.server.get("/healthz")
    def healthz() -> tuple[str, int]:
        return "ok", 200

    return app
