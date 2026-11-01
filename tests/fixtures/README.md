# Fixture provenance

`ll2_public_sample.json` is a **reduced public-data subset**, acquired 2026-11-01 16:52:36–16:52:39 UTC from production LL2 2.3.0 with `include_suborbital=false`. It contains two earliest historical records (normal), three recent records (detailed), and one upcoming day-precision record (detailed). Unknown, verbose and image/media fields were removed to keep a small fixture. Source identifiers and the values tested are unchanged.

The fixture's envelope (`count=6`, `next=null`) describes the saved subset, not the original API extraction or a complete historical/upcoming window. Import it using the last acquisition timestamp, or a later explicitly documented observation envelope. Full response checksums, query and observation metadata: docs/source_verification.json. Unmodified original bodies stay in ignored data/source-spike on the bootstrap machine.

`ll2_statuses.json` retains the public status vocabulary returned by `/config/launch_statuses/?limit=100` in the same probe. These data are from [The Space Devs](https://thespacedevs.com/), subject to their [terms](https://github.com/TheSpaceDevs/Tutorials/blob/main/faqs/faq_TSD.md#terms-of-use); MIT covers project code, not an alternative license for source data/images.

Tests construct clearly labeled synthetic failures, partial/unresolved outcomes, rideshares, corrections, suborbital records, missing fields and coarse-date boundaries from copies. Those are not real launches and never enter real-run artifacts or project findings.

`ll2_history_1957.json` is an **unchanged complete API page** acquired 2026-11-01T17:04:49.556708+00:00 (request-start timestamp), via the installed CLI. Query: `/launches/previous/?limit=5&mode=detailed&include_suborbital=false&ordering=net&net__gte=1957-01-01T00:00:00Z&net__lt=1958-01-01T00:00:00Z`. It contains Sputnik 1, Sputnik 2 and Vanguard TV3. Exact response checksum and envelope: docs/history_fixture_provenance.json. This enables genuine raw-to-Parquet replay from a new checkout, including a real failure. No media assets are bundled; image URLs are retained only as source metadata.
