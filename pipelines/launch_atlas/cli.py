import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import polars as pl

from launch_atlas.config import Settings
from launch_atlas.pipeline import import_raw, ingest, replay, summary


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Space Launch Atlas: local LL2 raw storage and Parquet; no cloud writes."
    )
    commands = root.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Offline configuration and dependency check")
    doctor.add_argument("--offline", action="store_true", required=True)
    for name in ("smoke", "backfill"):
        command = commands.add_parser(
            name, help="Network: bounded detailed LL2 pages with durable resume"
        )
        command.add_argument("--source", choices=["ll2"], default="ll2")
        command.add_argument(
            "--max-requests", type=int, default=2, help="1–8 requests including retries"
        )
        command.add_argument(
            "--page-size", type=int, default=5, help="1–100 source records per page"
        )
        command.add_argument("--run-id", help="New run ID; required with --resume")
        command.add_argument(
            "--resume", action="store_true", help="Use saved query and next-page checkpoint"
        )
        if name == "smoke":
            command.add_argument(
                "--window", choices=["recent", "history", "upcoming"], default="recent"
            )
        else:
            command.add_argument("--start-year", type=int)
            command.add_argument(
                "--end-year", type=int, help="Inclusive; defaults to the current UTC year"
            )
    replay_command = commands.add_parser(
        "replay", help="Offline: verify raw checksums and rebuild this run"
    )
    replay_command.add_argument("--run-id", required=True)
    import_command = commands.add_parser(
        "import-raw", help="Offline: import a saved, explicitly orbital LL2 JSON page"
    )
    import_command.add_argument("--input", type=Path, required=True)
    import_command.add_argument("--run-id", required=True)
    import_command.add_argument(
        "--observed-at", required=True, help="Actual acquisition timestamp with timezone"
    )
    import_command.add_argument(
        "--orbital-only",
        action="store_true",
        required=True,
        help="Assert provenance: include_suborbital=false was used",
    )
    for command in (
        doctor,
        *[commands.choices[name] for name in ("smoke", "backfill", "replay", "import-raw")],
    ):
        command.add_argument(
            "--data-dir",
            type=Path,
            help="Local raw/checkpoint/Parquet root; default ./data or LOCAL_DATA_DIR",
        )
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    overrides: dict[str, object] = {}
    if arguments.data_dir is not None:
        overrides["data_dir"] = arguments.data_dir
    if arguments.command in ("smoke", "backfill"):
        overrides.update(max_requests=arguments.max_requests, page_size=arguments.page_size)
    try:
        settings = Settings.from_env(**overrides)
        if arguments.command == "doctor":
            print(
                json.dumps(
                    {
                        "mode": settings.mode,
                        "python": sys.version.split()[0],
                        "source": settings.base_url,
                        "data_dir": str(settings.data_dir.resolve()),
                        "dependencies": {
                            name: version(name)
                            for name in ("space-launch-atlas", "httpx", "pydantic", "polars")
                        },
                        "network_used": False,
                        "cloud_configured": False,
                    },
                    indent=2,
                )
            )
            return 0
        if arguments.command == "replay":
            quality = replay(settings, arguments.run_id)
            print(json.dumps(quality, indent=2))
            return 0 if quality["passed"] else 1
        if arguments.command == "import-raw":
            manifest = import_raw(
                settings, arguments.input, arguments.run_id, arguments.observed_at
            )
        else:
            now = datetime.now(UTC)
            start, end = None, None
            if arguments.command == "backfill":
                window = "history"
                if not arguments.resume:
                    first, last = arguments.start_year, arguments.end_year or now.year
                    if first is None or not 1957 <= first <= last <= now.year:
                        raise ValueError(
                            "Choose 1957 <= --start-year <= --end-year <= current UTC year"
                        )
                    start, end = f"{first}-01-01T00:00:00Z", f"{last + 1}-01-01T00:00:00Z"
            else:
                window = arguments.window
                if not arguments.resume:
                    if window == "upcoming":
                        start, end = now.isoformat(), (now + timedelta(days=90)).isoformat()
                    elif window == "recent":
                        start, end = (now - timedelta(days=30)).isoformat(), now.isoformat()
                    else:
                        start, end = "1957-01-01T00:00:00Z", "1958-01-01T00:00:00Z"
            manifest = ingest(
                settings,
                window=window,
                start=start,
                end=end,
                run_id=arguments.run_id,
                resume=arguments.resume,
            )
        print(summary(manifest))
        if manifest["status"] in ("failed", "normalization_failed", "validation_failed"):
            return 1
        # 2 = safely incomplete, suitable for scripts; no publication eligibility is implied.
        return 0 if manifest["complete"] else 2
    except (ValueError, OSError, pl.exceptions.PolarsError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
