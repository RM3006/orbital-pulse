# Local setup

Use this repository as the working directory. Python **3.12** and **uv** are required. Tested on Windows with Python 3.12.13 and uv 0.11.17. The lockfile contains runtime and dev dependencies; a first install needs package-registry access. uv's project cache is .uv-cache (ignored). No GCP account, API token or billing is needed. Linux CI is defined but has not yet executed remotely.

```powershell
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -m "not integration and not live" --basetemp .pytest-tmp
uv run python -m launch_atlas --help
uv run python -m launch_atlas doctor --offline
```

Those commands work in PowerShell and standard Linux shells. `.pytest-tmp` is reserved for pytest's temporary files and may be cleared by pytest. Do not put project data there. Its parent is the existing checkout root, so a fresh checkout does not require a pre-created data directory. Tests import the installed package; no sys.path manipulation or PYTHONPATH is required. Offline tests block socket connections. `integration`/`live` are registered marker reservations; no warehouse integration or live API test runs in default CI.

Reproduce a genuine saved 1957 slice without network:

```powershell
uv run python -m launch_atlas import-raw --input tests/fixtures/ll2_history_1957.json --observed-at 2026-11-01T17:04:49.556708+00:00 --orbital-only --run-id demo-1957
uv run python -m launch_atlas replay --run-id demo-1957
```

`--orbital-only` asserts the source query provenance, not that every record is ultimately a qualifying orbital attempt. The original page and metadata stay under data/raw/ll2/demo-1957; ten Parquet tables and quality.json appear under data/normalized/demo-1957. Re-running import with an existing ID refuses to overwrite; use replay for idempotent reconstruction. Historical sample expectation: 3 attempts (2 successes, 1 failure), source NET dates, missing precision retained. This small year is not a current worldwide reliability estimate.

Explicit live access (up to two requests, including retries):

```powershell
uv run python -m launch_atlas smoke --source ll2 --max-requests 2
uv run python -m launch_atlas smoke --window upcoming --page-size 20 --max-requests 2
uv run python -m launch_atlas backfill --start-year 1957 --end-year 1957 --page-size 100 --max-requests 2 --run-id history-example
```

Smoke defaults: five detailed records/page; recent = previous endpoint for the last 30 days; history = 1957 only; upcoming = rolling next 90 days. Larger pages can be slow (100-record upcoming request timed out once during bootstrap). Maximum page size 100 and budget 8; every retry counts. A budget stop returns a saved run ID and next URL. Exit codes: 0 = completed requested extraction or successful offline check, 2 = safely incomplete/checkpointed, 1 = failure/validation failure. Passing quality on a partial run does not make its window complete.

Resume that exact run, without changing its frozen source query:

```powershell
uv run python -m launch_atlas backfill --resume --run-id history-example --max-requests 2
```

`--page-size` and time-window options do not replace a resumed run's saved query. Completed resume uses no network. `replay` always uses raw artifacts only. `refresh`, warehouse `validate` and `publish` are intentionally absent until their phases exist; help does not advertise stubs.

Configuration: .env.example documents actual environment variables. The application **does not automatically load .env files**. For example in PowerShell: `$env:LOCAL_DATA_DIR = './data'`; on Linux set/export the same variable. Only local mode and the verified production LL2 source are accepted. Free unauthenticated API access only; no token is read or required. Credentials, local state and raw data are ignored by Git. Supply a descriptive User-Agent, replacing the contact text with a real public project URL after one exists.

Packaging: `uv build --wheel`. The wheel includes pipelines/launch_atlas as importable launch_atlas plus the `launch-atlas` executable. No dashboard/server process is needed. New checkouts can reproduce the offline slice from the committed raw fixture. Cross-platform CI uses this same root pyproject.toml/uv.lock.
