# Space Launch Atlas · Printed Atlas design specification

Reproduce the approved blue Printed Atlas edition using the [standalone HTML](printed-atlas-design-export/printed-atlas.html) and the five PNGs linked below. The [prototype markup, CSS and rendering code](printed-atlas-prototype.html) define the details. The companion [data specification](printed-atlas-data-spec.md) maps each visual to the exact BigQuery fields, joins and denominators. Example copy and numbers below refer to the **11 Nov 2026 UTC** edition. Keep dates, narrative claims and numbers tied to the same release when refreshing the data.

All dimensions are CSS pixels unless otherwise specified. The reference PNGs are captured at **1,440 CSS px wide**, exported at **2×** density. Fluid values and responsive breakpoints are defined here so the design can be reproduced at other widths.

## Global design

### Colour and shape

| Token or surface | Exact value | Use |
| --- | --- | --- |
| Page | `#F5F6F8` | Full atlas canvas, masthead and map background. |
| Surface | `#FFFFFF` | Cards, tables, selects, dropdown options and tooltip. |
| Ink | `#202329` | Headlines, body, table cells, values and strong findings. Use for the table header's bottom rule as well. |
| Muted text | `#505761` | Secondary copy, table header text, axes, control labels, inactive tabs and edition/footer text. |
| Atlas blue | `#223DA4` | Masthead rule and square, active tab, chapter label, selected marks and disclosure links. |
| Line | `#D9DEE6` | Card edges, dividers, table row rules and plot reference lines. |
| Track | `#EBEDF2` | Schedule window tracks and the low end of the monthly heatmap. |
| Secondary mark | `#818BA7` | Unhighlighted bars and select borders. |
| Schedule interval | `#AAB7E4` | Shaded possible-date ranges. |
| Map land / outline | `#E0E5ED` / `#C2CAD7` | Natural Earth land fill and boundary stroke. |
| Inactive map circle | `#8192CE` at 74% fill opacity | White outline. The active circle uses opaque Atlas blue. |
| Flight baseline | `#A0A8B8` | Baseline of the booster flight strip. |

- Cards use white fill, a **1 px solid** line border and an **8 px** corner radius. This applies to full-width visuals, supporting panels, both halves of a paired row, and the two Upcoming disclosure cards. Use an unshadowed card surface. The table inherits its outer corners from the card.
- Selects and the map's inner plotting area have a **4 px** radius. The logo square, blue navigation underline, rectangular bars, heatmap cells and schedule tracks have square edges. Map markers are circles.
- Keep all text on light surfaces dark. In particular, table cells and their contents use Ink, and table headings use Muted text. Keep the table white with subtle horizontal rules.
- The prototype also declares `#D8DCEF` as a pale token. No approved visual uses it. The standalone tooltip uses `#20232920` for its shadow only.

### Typography

Load the original fonts. The standalone export embeds the font files. Use the following fallbacks in order.

| Role | Family and fallbacks | Weight · size / line height | Other settings |
| --- | --- | --- | --- |
| Body and table prose | Instrument Sans, Arial, sans-serif | 400 · 14 px / 1.45 | Default Ink. Table prose overrides size to 13 px. |
| Wordmark | Archivo Narrow, Arial Narrow, Arial, sans-serif | 700 · 23 px / 0.98 | Tracking `0.015em`. |
| Chapter headline | Archivo Narrow, Arial Narrow, Arial, sans-serif | 700 · `clamp(30px, 4.5vw, 36px)` / 1.02 | Tracking `−0.018em`. Overview overrides the size to `clamp(33px, 5.5vw, 44px)`. |
| Overview large number | Archivo Narrow, Arial Narrow, Arial, sans-serif | 700 · `clamp(33px, 5.3vw, 69px)` / 0.9 | Atlas blue with tabular numerals. |
| Card title | Instrument Sans, Arial, sans-serif | 700 · 19 px / 1.15 | Tracking `−0.015em`. |
| Chapter label, edition and footer | IBM Plex Mono, Consolas, monospace | 500 · 11 px / 1.4 | Chapter label and edition are uppercase via CSS with `0.05em` tracking. Footer uses sentence case. |
| Chapter deck | Instrument Sans, Arial, sans-serif | 400 · 14 px / 1.45 | Muted text, width at most `52ch`. |
| Card subtitle and notes | Instrument Sans, Arial, sans-serif | 400 · 12 px / 1.45 | Muted text. |
| Navigation | Instrument Sans, Arial, sans-serif | 600 · 13 px / 1.15 | Muted text and blue active state. |
| Control label / select value | Instrument Sans, Arial, sans-serif | 400 · 13 px / 1.45 for label, 500 · 13 px / 1.2 for select | Muted label, Ink value. |
| Table header / row label | Instrument Sans, Arial, sans-serif | 600 · 12 px for header, 500 · 13 px for row label | Table line height inherits 1.45. |
| Table numbers | IBM Plex Mono, Consolas, monospace | 400 · 13 px / 1.45 by default | Right-aligned, tabular and no wrap. Totals are 700. |
| Findings / disclosure summary | Instrument Sans, Arial, sans-serif | 600 · 14 px / 1.45 | Ink finding, Atlas blue summary. |

### Canvas, masthead, and navigation

- Use one full-width, light-colour-scheme atlas. The standalone document has zero outer body margin. Set `box-sizing: border-box` throughout the atlas. Reset default margins on H2, H3, paragraphs, figures, definition lists and their values.
- Shared horizontal inset is **`clamp(18px, 4.2vw, 47px)`**. Apply it to masthead, navigation, main and footer. Main padding is **25 px top and bottom**. At 1,440 px wide, page content starts at x = 47 px and has 1,346 px available. Cards are fluid within that column.
- Masthead starts with a **6 px solid Atlas blue rule** across the full viewport. Inside it, use **18 px top / 13 px bottom** padding. Keep the wordmark left and edition right with centered vertical alignment and **6 px row / 18 px column** gap on wrap. Wordmark is an **11 × 11 px** blue square followed by “Space Launch Atlas” with an **8 px** gap. Edition reads “Data as of 11 Nov 2026 · UTC”; the font renders it in uppercase.
- Directly below, use a wrapping row of text buttons in this sequence: **Overview**, **Providers & vehicles**, **Spaceports**, **Reuse & landings**, **Upcoming & coverage**. Gap is **24 px** horizontally and **0 px** vertically. Navigation has a **1 px** line along the bottom. Buttons have transparent backgrounds, **11 px top / 9 px bottom** padding, **38 px minimum height**, and a reserved **3 px** transparent bottom border. The active tab changes text and border to Atlas blue. Its underline meets the navigation rule with the button's `margin-bottom: -1px`. Mark the active tab with `aria-current="page"` and show one chapter at a time. Overview opens by default.
- Footer follows the visible page. Use a **1 px** top line, **9 px top / 13 px bottom** padding, and **4 px row / 12 px column** gap. Put “Source · Launch Library 2” on the left and “Production data cut · 11 Nov 2026” on the right. Let it wrap at narrow widths.

### Chapter openings, cards, and controls

- Every chapter opens with a blue uppercase label, a headline, then a muted deck. Leave **7 px** between the label box and headline, **10 px** from headline to deck, and **18 px** between the deck block and the first card. Overview uses **23 px** below its opening and includes a right-side callout.
- Card padding is **`clamp(12px, 2vw, 20px)`**. A standard stacked supporting card starts **23 px** after the preceding block. A row of paired cards starts **24 px** after the main visual. Its grid is **`minmax(0, 1.4fr) minmax(0, 1fr)`** with **32 px column / 21 px row** gaps. Cards in a desktop pair stretch to the same row height.
- A card header with a control uses a wrapping flex row, first child left and control right, aligned at the top. Use **8 px row / 16 px column** gaps and **12 px** below the header. The subtitle starts **4 px** after the card title. Standalone notes also start with **4 px** top margin. Put a strong finding or readout **12 px** below the preceding element.
- Place the label beside the native select. Center them vertically and allow wrapping with **6 px row / 8 px column** gaps. Select padding is **6 px vertical / 7 px horizontal**, border **1 px solid `#818BA7`**, and max width **100%**. Dropdown options use the same dark text and white background. Keep the browser's select affordance.

### Tables, small bars, tooltips, and disclosures

- Tables are full width with collapsed borders and **8 px** top margin. Cells use **8 px vertical / 6 px horizontal** padding with a **1 px** bottom line. The first column is left aligned, can wrap anywhere, and has no left padding. The last column has no right padding. Other columns default to right alignment unless a page specifies otherwise. Body cells align to the top, headers to the bottom. The header row has an Ink rule underneath. Totals use bold text and no bottom rule. Where a table has a scroll wrapper, use `max-width: 100%; overflow-x: auto`.
- Horizontal bar lists start **9 px** below the preceding element and have **7 px** between rows. Each row is a three-column grid, **7ch label / flexible bar area / 4ch right-aligned number**. Center the row vertically with **8 px** gaps. Labels are 12 px, numbers are IBM Plex Mono 500, 12 px / 1.2. Bars are **9 px** high in `#818BA7`. Highlighted bars use Atlas blue and **12 px** height. Width expresses value against that card's shared maximum; the remainder is white space.
- Use native disclosures with a visible marker and blue summary. Inline table disclosures start **10 px** below the preceding content. The two standalone Upcoming disclosure cards also use **10 px** top margin, overriding the standard 23 px rule. Start all disclosures closed.
- The standalone export's tooltips have white fill, Ink text, a **1 px `#D9DEE6`** border, **8 px** radius, **6 × 9 px** padding, and Instrument Sans 12 px / 1.4. Max width is `min(360px, calc(100vw - 16px))`. The soft shadow is `0 2px 8px #20232920`. Anchor near the pointer or focused mark and keep an 8 px viewport inset. Use plain text labels and keep chart values available in the accompanying table or accessible summary.
- Display unsupported or missing numeric values as an em dash. A known count of zero is a visible `0`. Where a date is shown, retain its source precision and UTC reference.

### Responsive rules

| Condition | Layout change |
| --- | --- |
| Width ≤ 620 px | Navigation horizontal gap becomes 16 px. Paired cards stack, keeping 21 px between rows. Overview's opening becomes one column. The large number and caption form an inline baseline-aligned row with 8 px gap and zero intervening margin; remove its desktop divider and left padding. Reliability switches to its compact table and per-row outcome disclosure. Spaceports heatmap labels drop to 11 px, cell gap to 1 px. Upcoming manifest switches to its stacked row layout described below. |
| Width ≤ 450 px | Annual chart bar and year-grid gaps become 1 px. Hide secondary year labels. Keep the start year and 2025, plus the 2020 axis label in the default history view. In the full-history view hide the 2020 axis label as well. The 2020 and 2025 annotations on the bars stay visible. In the manifest's time axis, hide the second and fourth labels. |
| Coarse pointer | Tabs, selects and disclosure summaries have a 44 px minimum height. |

## 1. Overview

[Desktop reference · Overview](printed-atlas-design-export/01-overview.png)

1. **Opening story.** Label “Orbital launch activity”. Headline is “Launch attempts” then “nearly tripled”, with a deliberate line break. Deck reads “Recorded attempts rose from 113 in 2020 to 324 in 2025. SpaceX accounted for 140 of the 211 additional attempts.” On desktop, use a bottom-aligned opening grid of **`minmax(0, 1fr) minmax(170px, 26%)`** with **12 px row / 32 px column** gaps. The right column starts with a **1 px** vertical divider and **16 px** left padding. Put the blue **324** above the 12 px muted caption “recorded attempts in 2025” with **7 px** between them.
2. **Orbital attempts by year.** Full-width card with subtitle “Complete UTC calendar years” and **History** selector in the upper right. Options are **Since 2000** (default) and **Since 1957**. The selector redraws this plot only. Show complete years through 2025. Plot has a **32 px** left axis and **5 px** gap to the bars, with a **185 px** tall y-axis and plot. Y labels are **350, 175, 0**, IBM Plex Mono 400, 11 px / 1. Keep 1 px rules at the upper and lower edges and an extra 1 px rule at mid-height. Use equal-width year columns, **3 px** gaps in the default view and **1 px** in full history. Bars use `#818BA7` through 2019 and Atlas blue from 2020. Add “2020 · 113” and “2025 · 324” above their bars, right-aligned to the bar edge, blue, IBM Plex Mono 500, 11 px / 1.4, no wrap. Below, use a 4 px top inset and muted mono 11 px / 1.35. Default ticks are 2000, 2005, 2010, 2015, 2020, 2025; full-history ticks are 1957, 1970, 1985, 2000, 2010, 2020, 2025. Place labels on the equal-width year grid, with the first and last labels aligned to the outer edges. Hover text is “{year} · {count} attempts”.
3. **Who drove the change?** Left card in the paired row. Subtitle “Recorded attempts by provider, 2020 and 2025”. Table columns **Provider**, **2020**, **2025**, **Change**. Show SpaceX, China Aerospace Science and Technology Corporation, Rocket Lab, Arianespace, Astra Space, and All other providers in that order. Use right-aligned mono numerals and explicitly signed changes. Bold the SpaceX change in Ink. End with a bold **Total** row, **113 / 324 / +211**.
4. **So far this year.** Right card. Subtitle “1 January to 10 November UTC in each year”. Four horizontal bars with labels and counts **2023 / 182**, **2024 / 208**, **2025 / 265**, **2026 / 260**, scaled to a common **300**. Highlight 2026 in blue using the taller 12 px mark. Below the bars place the Ink finding “Five fewer attempts than in 2025.”

## 2. Providers & vehicles

[Desktop reference · Providers & vehicles](printed-atlas-design-export/02-providers-and-vehicles.png)

1. **Opening story.** Label “Providers & vehicles”. Headline “The sample size changes the story”. Deck “Falcon 9 Block 5 recorded 642 successes in 643 classified attempts. Soyuz 2.1a recorded 51 in 51.”
2. **Launch outcomes.** The page's only visual is one full-width card. Put the **Group by** selector at the upper right. Options in order are **Configuration** (default), **Vehicle family**, **Provider**. Below the title, default subtitle is “Since 2000 · ranked by attempt count”. Family view changes it to “Since 2000 · family membership can overlap”. Group changes update the table, subtitle and overflow count. The opening story stays in place.
3. **Table.** Headers in order: the selected group label (**Configuration**, **Vehicle family** or **Provider**), **Successes / classified**, **Success rate**, **Failure**, **Partial**, **Unresolved**. Left-align group names and set the five numeric columns in right-aligned mono. Give the numerator/denominator cell weight **600**. Display the rate to one decimal with `%`. Show the top **eight** groups, then a single “Show {N} more groups” disclosure in the same card. The second table repeats the headers when opened. Follow the rank and ID rules in the data spec so same-named configurations remain separate rows.
4. **Compact table at ≤ 620 px.** Keep the group name and **Successes / classified** columns. Below each name, provide a collapsed **Outcome breakdown** disclosure with a definition list in order: **Success rate**, **Failures**, **Partial failures**, **Unresolved**. Use muted, weight 400 labels and **5 px** spacing below each value. Keep the same row order and eight-row default.

## 3. Spaceports

[Desktop reference · Spaceports](printed-atlas-design-export/03-spaceports.png)

1. **Opening story.** Label “Spaceports”. Headline “About four days between attempts at the two busiest sites”. Deck “Since January 2024, the median observed gap was 4.1 days at Cape Canaveral SFS and 4.5 days at Vandenberg SFB.”
2. **Where the busiest spaceports are.** Full-width map card. Subtitle “Six sites ranked by recorded orbital attempts since January 2024”. Place the **Site** select on the right. Featured sites, in order: Cape Canaveral SFS (default), Vandenberg SFB, Jiuquan Satellite Launch Center, Kennedy Space Center, Wenchang Space Launch Site, Rocket Lab Launch Complex 1. Shorten map and selector labels before their first comma. Use the embedded Natural Earth land path and projected points from the prototype to match the reference. SVG viewBox **`0 0 698 339`**, width **100%**, max height **265 px**, page-grey fill behind the geometry and **4 px** corners. Stroke the land with **0.6 SVG units**. Circles encode attempt counts by area, with radius `8 × sqrt(site attempts / top site attempts)` in SVG units. Inactive circles have a **1-unit** white outline. Draw the selected circle last, blue, with a **2-unit** white outline. Clicking a point or choosing the site updates the marker and the readout immediately. Hover text shows the short site name and attempt count. Below the map, display “{site} · {attempts} attempts · {median} days median observed gap ({N} supported gaps)” as a strong finding, or “No supported gaps” when applicable. Follow with “Circle area represents attempt count. Basemap by Natural Earth.”
3. **Monthly activity at the busiest sites.** Next full-width card, **23 px** below the map. Subtitle “January 2024 to 11 November 2026 UTC · November is partial”. Six rows in the same site order. Each row uses **`minmax(100px, 23%) minmax(0, 1fr)`** for site label and month grid, with a **9 px** gap, centered vertically and **5 px** below each row. Use 12 px labels. Grid has **35 equal columns**, **2 px** gaps, **15 px** high cells. Scale fills with sRGB interpolation between Track and Atlas blue using `attempts / max`. In this edition the common maximum is **9**. Put 2024, 2025 and 2026 above columns **1, 13, 25** respectively, spanning six columns each. Year labels are muted IBM Plex Mono 400, 11 px / 1.3, with zero bottom margin. Hover shows “{site} · {month and year} · {count} attempts”. The map selection leaves all six heatmap rows visible.
4. **Heatmap legend and exact counts.** Put the legend **8 px** below the grid. Use centered, wrapping inline items with **4 px** gaps and mono 11 px / 1.2. Show **0**, then five **12 × 12 px** swatches at 0%, 25%, 50%, 75%, 100% of the scale, then **9** and “attempts per month”. Below it, a collapsed **Monthly counts** disclosure opens a horizontally scrollable table, **Month** then one numeric column per featured site. Include every month, even if a cell is zero.
5. **Time between observed attempts.** Full-width card **23 px** below the heatmap card. Place the note “Gaps end in the period shown and require supported precision on both launches.” under the title. Table headers **Site**, **Attempts**, **Median days**, **Supported gaps**. Use full site labels, rank by recorded attempts, right-align numbers, and display median to **one decimal**. Show eight rows first, then “Show {N} more sites” below them.

## 4. Reuse & landings

[Desktop reference · Reuse & landings](printed-atlas-design-export/04-reuse-and-landings.png)

1. **Opening story.** Label “Booster reuse”. Headline “Follow a booster across its flights”. Deck “Explore when each booster flew and how much time passed between supported consecutive flights.”
2. **Recorded flight history.** One full-width card with a **Serial number** select at the upper right. Options in order **B1067** (default), **B1063**, **B1071**, **B1069**, **B1077**, **B1078**. Under the title show “{N} recorded flights · {M} supported turnaround intervals”. The default displays **37 recorded flights · 29 supported turnaround intervals**. The selector updates this subtitle, the strip and the log together.
3. **Flight strip.** Inside the card above the log, use a **43 px** high track with **10 px top, 9 px horizontal, 2 px bottom** margin and a **1 px solid `#A0A8B8`** baseline. Place a **3 px wide × 25 px high** blue vertical tick for each recorded flight. Center each tick on its date position with a half-width offset. Scale positions proportionally from the selected booster's first to last recorded UTC date. Put Jan 1 ticks as year labels from the year after its first flight through its last flight year. Center labels on their date positions in a **15 px** high row with **4 px top, 9 px horizontal, 12 px bottom** margins. Year labels are muted mono 11 px / 1.3. Hover shows flight number, UTC date, launch name and supported turnaround when available.
4. **Flight log.** Directly below the strip in the same card. Columns **Launch**, **UTC date**, **Flight**, **Turnaround (days)**. Use day, abbreviated month and year for the UTC date; use right-aligned mono for flight number and days. Round days to one decimal. A missing flight number or turnaround shows an em dash. Show the seven most recent flights first. “Show {N} more earlier flights” reveals the rest in the same column order.
5. **Recorded landing outcomes.** Left card in the paired row. Subtitle “Separate landing attempts across all recorded hardware”. Table **Outcome / Landings** with **Successful 686**, **Failed 23**, **Unknown outcome 0**, then bold total **Landing attempts 709**. Keep it independent of the booster selector.
6. **Hardware coverage.** Right card. Use a title and one muted deck paragraph starting **10 px** below, max width **52ch**. Copy for the frozen edition: “636 of 776 flights with known reuse status were reused. SpaceX accounts for 745 of the 776 identifiable hardware flights. In all, 750 of 6,903 orbital attempts have an associated stage-flight record.” This paragraph describes the full edition and stays fixed during booster selection.

## 5. Upcoming & coverage

[Desktop reference · Upcoming & coverage](printed-atlas-design-export/05-upcoming-and-coverage.png)

1. **Opening story.** Label “Upcoming launch windows”. Headline “Most entries have only a launch year”. Deck “129 of the 179 entries overlapping the 90-day window of 11 Nov 2026 to 9 Feb 2027 have year-level precision.”
2. **Launch manifest header.** Full-width card. In the subtitle show the live count, “{N} entries overlap the frozen 90-day horizon · dates in UTC”; use “1 entry overlaps” when exactly one result is visible. Put the **Precision** select at the upper right. Default **All precisions**. Show categories present in source precision order. This edition has **Minute**, **Hour**, **Day**, **Month**, **Quarter**, **Year**. Below the header place the note “Shaded intervals show possible dates within the report horizon. Ticks mark entries timed to the minute or second. Status appears beside each launch.”
3. **Manifest table.** Columns **Launch**, **Provider**, **Source status**, **Reported window**, all left aligned. Launch is the row heading. Use “Unknown provider” for a missing provider and put “Orbital scope unknown” beneath the source status when applicable. The final column is **29%** of the desktop table, with a **140 px** minimum for the window block. The reported date or period appears above its track. Show seven rows by default, then “Show {N} more entries” with a matching table and the same axis. The precision selection updates the rows and count together, keeping the horizon fixed.
4. **Reported window mark.** The time track is **13 px** high with **4 px** top and bottom margin. Background is Track with a repeating horizontal gradient that puts a **1 px** Line-coloured vertical rule at each quarter of the shared 90-day range. Draw a possible interval in `#AAB7E4` at its clipped start position, **2 px** inset from top and bottom, using its clipped duration as width with **1 px** minimum. For minute/second precision draw a **2 px** wide, full-height Atlas blue tick. Use the real precision in the label, for example “2026 · year only”, “Q4 2026”, a month and year, a date with “· day only”, an hour range, or a UTC timestamp at minute/second precision. Hover reveals both original UTC bounds and identifies the end as exclusive.
5. **Shared axis.** On desktop put it immediately under the **Reported window** header. Use five evenly spaced date labels across the 90-day horizon at **0%, 25%, 50%, 75%, 100%**, formatted as day and abbreviated month in UTC. Align first and last to the edges. Axis has **5 px** top margin and **2 px** minimum gap, uses flex space-between, and stays on one line in muted IBM Plex Mono 400, **11 px / 1.2**. Both the marks and header use the same horizon domain. Keep the precision track rather than reducing the visual to date strings.
6. **Entries by date precision.** First standalone disclosure card, **10 px** below the manifest. Start closed. When open, show horizontal bars in precision order with labels **Minute 8**, **Hour 6**, **Day 5**, **Month 19**, **Quarter 12**, **Year 129**. Use the shared bar treatment and blue emphasis on Year. The list is at most **580 px** wide and uses **129** as the full scale for this edition. It describes the whole horizon during precision filtering.
7. **Coverage and methodology.** Second standalone disclosure, **10 px** below the previous card. Start closed. Use a definition list max width **68ch**, **9 px** vertical outer margin, and **6 px** vertical / **20 px** horizontal grid gaps. Each line aligns label and value at the baseline, uses space-between and at least **12 px** gap. Values in mono 500, **12 px / 1.3**. Rows, in order: **Source records 7,683**, **Recorded orbital attempts 6,903**, **Source records with unknown orbital scope 556**, **Manifest entries with unknown orbital scope 21**, **Eligible-status records without supported date precision 34**. Beneath the list show “The 34 records lack a supported schedule interval. Their overlap with the horizon is unknown. Launch Library 2 is a community-maintained source.”
8. **Stacked manifest at ≤ 620 px.** Visually hide the shared table head while retaining its semantics. Each manifest row becomes a grid of **`minmax(0, 3fr) minmax(0, 2fr)`** with **3 px row / 10 px column** gaps, **9 px vertical** padding and a **1 px** row divider. Clear individual cell rules and padding. Launch spans both columns in weight **700**. Provider and source status share the next line. Reported window spans both columns with **4 px** top margin. Show its date axis beneath the track on every row. At ≤ 450 px show the first, middle and last date labels only.

## Design acceptance checks

- Compare all five pages to their reference PNGs at 1,440 CSS px with the fonts loaded. Check the active tab, opening story, first card, supporting panels and source footer. Keep the same content hierarchy and colour roles.
- At 620 px and 450 px, verify that the corresponding layouts apply, especially the compact outcomes table, stacked pairs, and the manifest's per-row axes.
- Open every disclosure. Change Overview history, outcomes grouping, map site, booster, and manifest precision. Check that each control changes the specified visuals and that the expanded tables match their compact versions.
- On Upcoming, compare a fine-precision tick and a broad interval. On Reuse, pick a second booster and confirm the spacing of the ticks changes with its flight dates. Check dark table text on white and under every interactive state.
