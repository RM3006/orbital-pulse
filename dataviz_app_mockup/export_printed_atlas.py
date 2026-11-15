"""Package the existing Printed Atlas design without changing its page layouts."""

import base64
import hashlib
import re
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "printed-atlas-prototype.html"
OUTPUT = ROOT / "printed-atlas-design-export"
PAGES = {
    "overview": "01-overview.png",
    "vehicles": "02-providers-and-vehicles.png",
    "sites": "03-spaceports.png",
    "reuse": "04-reuse-and-landings.png",
    "schedule": "05-upcoming-and-coverage.png",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
TOOLTIP = """
<script>
(() => {
  const atlas = document.getElementById('printed-launch-atlas');
  const tooltip = document.createElement('div');
  tooltip.className = 'tooltip';
  tooltip.hidden = true;
  tooltip.setAttribute('role', 'tooltip');
  document.body.append(tooltip);
  let anchor = null;
  function hide() { tooltip.hidden = true; anchor = null; }
  function show(element, x, y) {
    anchor = element;
    tooltip.textContent = anchor.dataset.tooltip;
    tooltip.hidden = false;
    const left = Math.max(8, Math.min(x + 12, innerWidth - tooltip.offsetWidth - 8));
    const top = y + 14 + tooltip.offsetHeight <= innerHeight
      ? y + 14 : Math.max(8, y - tooltip.offsetHeight - 12);
    Object.assign(tooltip.style, {left: left + 'px', top: top + 'px'});
  }
  atlas.addEventListener('pointermove', (event) => {
    const target = event.target.closest('[data-tooltip]');
    if (target) show(target, event.clientX, event.clientY);
    else if (anchor) hide();
  });
  atlas.addEventListener('pointerleave', hide);
  atlas.addEventListener('focusin', (event) => {
    const target = event.target.closest('[data-tooltip]');
    if (!target) return;
    const rect = target.getBoundingClientRect();
    show(target, rect.left, rect.bottom);
  });
  atlas.addEventListener('focusout', hide);
  document.addEventListener('scroll', hide, true);
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') hide(); });
})();
</script>
"""


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=15) as response:
        return response.read()


def standalone(fragment: str) -> str:
    pattern = r"@import url\('(?P<url>https://fonts\.googleapis\.com/[^']+)'\);"
    match = re.search(pattern, fragment)
    if match is None:
        raise ValueError("The prototype's font stylesheet was not found")
    css = fetch(match["url"]).decode("utf-8")
    font_urls = set(re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", css))
    if not font_urls:
        raise ValueError("No WOFF2 font files were returned")
    for url in sorted(font_urls):
        data = base64.b64encode(fetch(url)).decode("ascii")
        css = css.replace(url, "data:font/woff2;base64," + data)
    fragment = fragment[: match.start()] + css + fragment[match.end() :]
    notices = []
    for family in ("archivonarrow", "instrumentalsans", "ibmplexmono"):
        font_folder = "instrumentsans" if family == "instrumentalsans" else family
        url = f"https://raw.githubusercontent.com/google/fonts/main/ofl/{font_folder}/OFL.txt"
        notice = fetch(url).decode("utf-8")
        notices.append("\n".join(line.rstrip() for line in notice.splitlines()).strip())
    # Include the SIL OFL notices directly in the standalone file.
    attribution = "\n\n".join(notices).replace("--", "—")
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Space Launch Atlas | Printed Atlas</title>\n"
        "<style>body{margin:0;background:#f5f6f8}"
        "body > .tooltip{position:fixed;z-index:10;pointer-events:none;"
        "padding:6px 9px;font:12px/1.4 'Instrument Sans',Arial,sans-serif;"
        "max-width:min(360px,calc(100vw - 16px));box-shadow:0 2px 8px #20232920}"
        "</style>\n</head><body>\n"
        f"<!-- Embedded font notices\n{attribution}\n-->\n"
        f"{fragment}\n{TOOLTIP}\n</body></html>\n"
    )


def capture(html: Path) -> None:
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=True,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1080},
            device_scale_factor=2,
            offline=True,
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(html.as_uri(), wait_until="load")
        page.evaluate("document.fonts.ready")
        for chapter, filename in PAGES.items():
            page.locator(f'[data-page="{chapter}"]').click()
            panel = page.locator(f'[data-chapter="{chapter}"]')
            assert panel.is_visible(), chapter
            assert page.locator("[data-chapter]:visible").count() == 1, chapter
            assert panel.locator("h2").inner_text().strip(), chapter
            assert panel.locator('tbody tr, svg, [role="img"]').count() > 0, chapter
            page.mouse.move(0, 0)
            page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
            page.evaluate("document.fonts.ready")
            page.locator("#printed-launch-atlas").screenshot(path=OUTPUT / filename)
        assert page.evaluate("""() => ['Archivo Narrow','Instrument Sans','IBM Plex Mono']
          .every(family => [...document.fonts].some(face =>
            face.family.replace(/['"]/g,'') === family && face.status === 'loaded'))""")
        page.select_option("#atlas-precision-select", "year")
        assert "129" in page.locator("#atlas-manifest-count").inner_text()
        page.locator('[data-page="reuse"]').click()
        selector = page.locator("#atlas-booster")
        before = selector.input_value()
        selector.select_option(index=1)
        assert selector.input_value() != before
        assert page.locator("#atlas-booster-timeline .atlas-flight-mark").count() > 0
        assert not errors, errors
        browser.close()


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    fragment = SOURCE.read_text(encoding="utf-8")
    html = OUTPUT / "printed-atlas.html"
    html.write_text(standalone(fragment), encoding="utf-8", newline="\n")
    capture(html)
    notes = OUTPUT / "README.txt"
    notes.write_text(
        "Space Launch Atlas | Printed Atlas design reference\n"
        "Design data cut: 11 Nov 2026 (UTC).\n\n"
        "Open printed-atlas.html in a browser to use all five pages and filters.\n"
        "The data, styles, and original three typefaces are embedded for offline viewing.\n"
        "The images are full-page captures at a 1440 CSS pixel desktop width, 2x density,\n"
        "with each page in its default state. Expandable sections remain available in the HTML.\n"
        "This is the agreed prototype design and the accompanying data is a frozen snapshot.\n\n"
        f"Original design source filename: {SOURCE.name}\n"
        f"Original design source SHA-256: {hashlib.sha256(SOURCE.read_bytes()).hexdigest()}\n",
        encoding="utf-8",
    )
    with ZipFile(ROOT / "printed-atlas-design.zip", "w", ZIP_DEFLATED) as archive:
        for asset in (html, notes, *(OUTPUT / name for name in PAGES.values())):
            archive.write(asset, asset.name)
    print("Verified five desktop captures, both filters, and the embedded fonts while offline.")


if __name__ == "__main__":
    main()
