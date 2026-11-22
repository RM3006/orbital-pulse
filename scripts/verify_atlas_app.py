"""Opt-in browser acceptance checks against a running local Dash app."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def ready_map(page) -> None:
    page.wait_for_function(
        """() => {
      const graph = document.querySelector('#atlas-map-graph .js-plotly-plot');
      const canvas = document.querySelector('#atlas-map-graph .maplibregl-canvas');
      const map = graph?._fullLayout?.map?._subplot?.map;
      const frame = document.querySelector('#atlas-map-graph');
      return map && canvas && map.loaded() && !map.getRenderWorldCopies() &&
        Math.abs(canvas.getBoundingClientRect().height - frame.clientHeight) < 2 &&
        Math.abs(canvas.getBoundingClientRect().width -
                 document.querySelector('#atlas-map-graph').clientWidth) < 2;
    }""",
        timeout=45000,
    )


def verify(url: str, output: Path) -> dict:
    if urlsplit(url).hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("This acceptance tool only targets the local app")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle")
        expect(page.locator(".atlas-mast")).to_be_visible(timeout=45000)
        page.evaluate("document.fonts.ready")
        font = page.locator("#printed-launch-atlas").evaluate("e => getComputedStyle(e).fontFamily")
        assert "Instrument Sans" in font
        assert (
            page.locator(".atlas-main").evaluate("e => getComputedStyle(e).paddingLeft") == "47px"
        )
        map_ids: list[str] = []
        site_options: list[dict[str, str]] = []
        for name in ("overview", "vehicles", "sites", "reuse", "schedule"):
            page.locator(f'[data-page="{name}"]').click()
            expect(page.locator(f'[data-chapter="{name}"]')).to_be_visible()
            expect(page.locator(f'[data-page="{name}"]')).to_have_attribute("aria-current", "page")
            if name == "sites":
                ready_map(page)
                map_ids = page.locator("#atlas-map-graph .js-plotly-plot").evaluate(
                    "e=>e.data[0].customdata.map(String)"
                )
                site_options = page.locator("#atlas-site-select option").evaluate_all(
                    "es=>es.map(e=>({id:e.value,name:e.textContent}))"
                )
                assert set(map_ids) == {item["id"] for item in site_options}
                assert len(map_ids) > 6
                assert page.locator("#atlas-map-graph .js-plotly-plot").evaluate("""e=>{
                  const map=e._fullLayout.map._subplot.map, data=e.data[0];
                  const box=map.getContainer().getBoundingClientRect();
                  return data.lat.every((lat,i)=>{
                    const point=map.project([data.lon[i],lat]);
                    return point.x >= 0 && point.x <= box.width &&
                           point.y >= 0 && point.y <= box.height;
                  });
                }""")
            page.screenshot(path=str(output / f"{name}-desktop.png"), full_page=True)

        schedule = page.locator('[data-chapter="schedule"]')
        original_count = page.locator("#atlas-manifest-count").inner_text()
        deck = schedule.locator(".atlas-deck").inner_text()
        match = re.match(r"([\d,]+) of", deck)
        assert match
        year_count = match.group(1)
        assert schedule.get_by_text("Entries by date precision", exact=True).count() == 0
        starts = schedule.locator("[data-window-start]").evaluate_all(
            "es => es.map(e => Date.parse(e.dataset.windowStart))"
        )
        assert starts == sorted(starts)
        assert year_count
        page.locator("#atlas-precision-select").select_option("year")
        expect(page.locator("#atlas-manifest-count")).to_contain_text(year_count)
        expect(schedule.locator(".atlas-manifest-window").first).to_contain_text("year only")
        expect(schedule.locator(".atlas-time-track i").first).not_to_have_attribute("data-exact")
        summary = schedule.locator(".atlas-disclosure > summary").first
        summary.click()
        assert schedule.locator("tbody tr:visible").count() == int(year_count.replace(",", ""))
        page.locator("#atlas-precision-select").select_option("")
        expect(page.locator("#atlas-manifest-count")).to_have_text(original_count)

        page.locator('[data-page="vehicles"]').click()
        expect(page.locator("#atlas-outcome-page-label")).to_contain_text("1–25 of")
        expect(page.locator(".atlas-outcome-table tbody tr")).to_have_count(25)
        page.locator("#atlas-outcome-next").click()
        expect(page.locator("#atlas-outcome-page-label")).to_contain_text("26–50 of")
        page.locator("#atlas-outcome-prev").click()
        page.locator("#atlas-outcome-size").select_option("0")
        expect(page.locator("#atlas-outcome-next")).to_be_disabled()
        assert page.locator(".atlas-outcome-table tbody tr").count() > 100
        page.locator("#atlas-outcome-search").fill("Falcon")
        page.locator("#atlas-outcome-search").press("Tab")
        expect(page.locator(".atlas-outcome-table tbody tr").first).to_contain_text("SpaceX")
        expect(page.locator(".atlas-entity-context").first).to_contain_text("Families:")
        page.locator("#atlas-outcome-search").fill("")
        page.locator("#atlas-outcome-search").press("Tab")
        page.locator("#atlas-reliability").select_option("family")
        expect(page.locator("#atlas-vehicle-note")).to_contain_text("overlap")
        expect(page.locator("#atlas-outcomes")).to_contain_text("Configurations:")
        page.locator("#atlas-reliability").select_option("provider")
        expect(page.locator("#atlas-outcomes thead th").first).to_have_text("Provider")
        page.locator("#atlas-reliability").select_option("configuration")
        page.locator("#atlas-outcome-size").select_option("25")

        page.locator('[data-page="reuse"]').click()
        assert (
            page.locator(
                "#atlas-reuse-provider, #atlas-reuse-family, #atlas-reuse-configuration"
            ).count()
            == 0
        )
        landing_before = page.locator('[data-chapter="reuse"] .atlas-pair').inner_text()
        serial_initial = page.locator("#atlas-booster").input_value()
        note_initial = page.locator("#atlas-booster-note").inner_text()
        flight_count = int(note_initial.split()[0].replace(",", ""))
        expect(page.locator(".atlas-flight-mark")).to_have_count(flight_count)
        expect(page.locator("#atlas-booster-context")).to_contain_text("Provider")
        expect(page.locator("#atlas-booster-context")).to_contain_text("Configuration")
        expect(page.locator("#atlas-booster-context")).to_contain_text("Vehicle families")
        context = page.locator(".atlas-booster-context")
        value = context.locator("dd").first
        label = context.locator("dt").first
        assert value.evaluate("e=>getComputedStyle(e).color") == "rgb(32, 35, 41)"
        assert label.evaluate("e=>getComputedStyle(e).color") == "rgb(107, 114, 128)"
        assert float(value.evaluate("e=>parseFloat(getComputedStyle(e).fontSize)")) > float(
            label.evaluate("e=>parseFloat(getComputedStyle(e).fontSize)")
        )
        context_width = context.evaluate("e=>e.getBoundingClientRect().width")
        assert context_width < 600
        assert value.evaluate("e=>e.getBoundingClientRect().top") < label.evaluate(
            "e=>e.getBoundingClientRect().top"
        )
        page.locator("#atlas-booster").select_option(label="B1063")
        expect(page.locator("#atlas-booster-note")).not_to_have_text(note_initial)
        selected_count = int(
            page.locator("#atlas-booster-note").inner_text().split()[0].replace(",", "")
        )
        expect(page.locator(".atlas-flight-mark")).to_have_count(selected_count)
        context.screenshot(path=str(output / "reuse-b1063-context.png"))
        page.locator("#atlas-booster-history .atlas-disclosure > summary").click()
        expect(page.locator(".atlas-flight-table tbody tr:visible")).to_have_count(selected_count)
        expect(page.locator('[data-chapter="reuse"] .atlas-pair')).to_have_text(
            landing_before, use_inner_text=True
        )
        page.locator("#atlas-booster").select_option(serial_initial)
        expect(page.locator(".atlas-flight-mark")).to_have_count(flight_count)

        page.locator('[data-page="schedule"]').click()

        def chart_total():
            return page.locator("#atlas-planned-chart [data-count]").evaluate_all(
                "es=>es.reduce((n,e)=>n+Number(e.dataset.count),0)"
            )

        total_entries = int(original_count.split()[0].replace(",", ""))
        assert chart_total() == total_entries
        page.locator("#atlas-planned-group").select_option("configuration")
        expect(page.locator("#atlas-planned-title")).to_contain_text("configuration")
        assert chart_total() == total_entries
        page.locator("#atlas-planned-group").select_option("family")
        expect(page.locator("#atlas-planned-note")).to_contain_text("do not sum")
        assert chart_total() >= total_entries
        page.locator("#atlas-planned-group").select_option("provider")
        page.locator("#atlas-planned-provider").select_option(label="SpaceX")
        expect(page.locator("#atlas-planned-chart .atlas-planned-row")).to_have_count(1)
        provider_count = chart_total()
        page.locator("#atlas-planned-group").select_option("configuration")
        expect(page.locator("#atlas-planned-title")).to_contain_text("configuration")
        assert chart_total() == provider_count
        page.locator("#atlas-planned-provider").select_option("")
        page.locator("#atlas-planned-group").select_option("provider")
        expect(page.locator("#atlas-planned-note")).to_contain_text(
            f"{total_entries:,} manifest entries"
        )
        page.locator("#atlas-precision-select").select_option("year")
        expect(page.locator("#atlas-planned-note")).to_contain_text(
            f"{year_count} manifest entries"
        )
        assert chart_total() == int(year_count.replace(",", ""))
        page.locator("#atlas-precision-select").select_option("")
        expect(page.locator("#atlas-planned-note")).to_contain_text(
            f"{total_entries:,} manifest entries"
        )

        page.locator('[data-page="sites"]').click()
        extra_site = site_options[6]
        page.locator("#atlas-site-select").select_option(extra_site["id"])
        expect(page.locator("#atlas-map-readout")).to_contain_text(extra_site["name"])
        ready_map(page)
        page.locator("#atlas-site-select").select_option(site_options[0]["id"])
        page.locator("#atlas-map-world").click()
        page.set_viewport_size({"width": 1900, "height": 1050})
        ready_map(page)
        assert page.locator("#atlas-map-graph .js-plotly-plot").evaluate(
            "e=>e._fullLayout.map._subplot.map.getRenderWorldCopies() === false"
        )
        page.screenshot(path=str(output / "single-world-wide.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1050})
        ready_map(page)
        page.locator("#atlas-map-florida").click()
        page.wait_for_function(
            "document.querySelector('#atlas-map-graph .js-plotly-plot').layout.map.zoom === 10"
        )
        ready_map(page)
        points = page.locator("#atlas-map-graph .js-plotly-plot").evaluate("""e => {
            let m=e._fullLayout.map._subplot.map, d=e.data[0];
            return ['12','27'].map(id=> {
              let i=d.customdata.indexOf(id); return m.project([d.lon[i],d.lat[i]]);
            });
        }""")
        separation = (
            (points[0]["x"] - points[1]["x"]) ** 2 + (points[0]["y"] - points[1]["y"]) ** 2
        ) ** 0.5
        assert separation > 80
        page.screenshot(path=str(output / "florida-close-up.png"), full_page=True)
        zoom_before = page.locator("#atlas-map-graph .js-plotly-plot").evaluate(
            "e=>e._fullLayout.map._subplot.map.getZoom()"
        )
        page.locator('#atlas-map-graph [data-title="Zoom in"]').click()
        page.wait_for_function(
            "document.querySelector('#atlas-map-graph .js-plotly-plot')"
            "._fullLayout.map._subplot.map.getZoom() > " + str(zoom_before)
        )
        page.locator("#atlas-map-florida").click()
        ready_map(page)
        page.wait_for_function(
            "document.querySelector('#atlas-map-graph .js-plotly-plot')"
            "._fullLayout.map._subplot.map.getZoom() === 10"
        )
        page.locator("#atlas-map-graph .maplibregl-canvas").click(position=points[1])
        expect(page.locator("#atlas-map-readout")).to_contain_text("Kennedy")
        expect(page.locator("#atlas-site-select")).to_have_value("27")
        page.locator("#atlas-site-select").select_option("11")
        expect(page.locator("#atlas-map-readout")).to_contain_text("Vandenberg")
        ready_map(page)
        canvas = page.locator("#atlas-map-graph .maplibregl-canvas")
        box = canvas.bounding_box()
        assert box
        center_before = page.locator("#atlas-map-graph .js-plotly-plot").evaluate(
            "e=>e._fullLayout.map._subplot.map.getCenter().lng"
        )
        page.mouse.move(box["x"] + 100, box["y"] + 100)
        page.mouse.down()
        page.mouse.move(box["x"] + 200, box["y"] + 100, steps=10)
        page.mouse.up()
        page.wait_for_function(
            "Math.abs(document.querySelector('#atlas-map-graph .js-plotly-plot')"
            "._fullLayout.map._subplot.map.getCenter().lng - " + str(center_before) + ") > .0001"
        )
        page.locator("#atlas-map-world").click()
        ready_map(page)

        page.locator('[data-page="overview"]').click()
        page.locator("#atlas-compare-start").select_option("2019")
        page.locator("#atlas-compare-end").select_option("2024")
        expect(page.locator("#atlas-overview-story")).to_contain_text("in 2019 to")
        expect(page.locator("#atlas-comparison thead th").nth(2)).to_have_text("2024")
        page.locator("#atlas-history").select_option("1957")
        page.wait_for_function(
            "document.querySelector('#annual-chart .js-plotly-plot').data[0].x[0] === 1957"
        )
        page.locator("#atlas-reload").click()
        expect(page.locator("#atlas-history")).to_have_value("2000")
        expect(page.locator("#atlas-compare-start")).to_have_value("2020")
        expect(page.locator("#atlas-compare-end")).to_have_value("2025")
        expect(page.locator("#atlas-booster")).to_have_value(serial_initial)
        expect(page.locator(".atlas-flight-mark")).to_have_count(flight_count)
        page.wait_for_function(
            "document.querySelector('#annual-chart .js-plotly-plot').data[0].x[0] === 2000"
        )

        for width in (620, 450, 375):
            page.set_viewport_size({"width": width, "height": 900})
            for name in ("overview", "vehicles", "sites", "reuse", "schedule"):
                page.locator(f'[data-page="{name}"]').click()
                expect(page.locator(f'[data-chapter="{name}"]')).to_be_visible()
                if name == "overview":
                    page.wait_for_function(
                        "Math.abs(document.querySelector('#annual-chart .main-svg')."
                        "getBoundingClientRect().width - "
                        "document.querySelector('#annual-chart').clientWidth) < 2"
                    )
                if name == "sites":
                    ready_map(page)
                if name in {"sites", "reuse"}:
                    table_kind = "site-gap" if name == "sites" else "flight"
                    row_title = page.locator(f".atlas-{table_kind}-table tbody th").first
                    assert row_title.evaluate("e => e.clientWidth") > width / 2
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"{name}-{width}.png"), full_page=True)
            assert schedule.locator(".atlas-row-axis").first.is_visible()
            page.locator('[data-page="vehicles"]').click()
            assert page.locator(".atlas-mobile-outcomes").first.is_visible()
        assert not errors, errors
        result = {
            "edition": page.locator(".atlas-edition").inner_text(),
            "manifest": original_count,
            "year_precision": year_count,
            "viewport_widths": [1440, 620, 450, 375],
            "pages": 5,
            "screenshots": 23,
            "mapped_sites": len(map_ids),
            "compact_context_width_px": round(context_width, 1),
            "wide_map_width": 1900,
            "world_copies": False,
            "planned_manifest_entries": total_entries,
            "kennedy_cape_separation_px": round(separation, 1),
            "serial_flight_history": note_initial,
            "javascript_errors": errors,
            "interaction_checks": "passed",
        }
        (output / "acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        browser.close()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8050")
    parser.add_argument("--output", type=Path, default=Path("data/app-review"))
    args = parser.parse_args()
    print(json.dumps(verify(args.url, args.output), indent=2))


if __name__ == "__main__":
    main()
