import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import polars as pl

from launch_atlas.config import Settings
from launch_atlas.pipeline import (
    import_raw,
    ingest,
    merge_run_state,
    rebuild_state,
    replay,
    run_plan,
    summary,
)
from launch_atlas.storage.exports import export_current
from launch_atlas.storage.snapshots import create_input_snapshot
from launch_atlas.storage.state import StateStore
from launch_atlas.warehouse.loading import (
    CloudTarget,
    GoogleBigQuerySourceLoader,
    execute_load_plan,
    prepare_load_plan,
)
from launch_atlas.windows import create_backfill_plan, create_reconcile_plan, create_refresh_plan


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Space Launch Atlas: local LL2 raw storage; cloud writes are explicit commands."
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
    state_merge = commands.add_parser(
        "state-merge", help="Offline: explicitly merge one complete validated raw run to SQLite"
    )
    state_merge.add_argument("--run-id", required=True)
    commands.add_parser("state-status", help="Offline persistent-state row counts")
    commands.add_parser(
        "state-export", help="Offline: validate and write a Parquet state generation"
    )
    commands.add_parser(
        "input-snapshot", help="Offline: verify and pin accepted Parquet/raw warehouse inputs"
    )
    warehouse_prepare = commands.add_parser(
        "warehouse-prepare",
        help="Offline: verify a snapshot and build a placeholder-safe load plan",
    )
    warehouse_prepare.add_argument("--snapshot-id", required=True)
    warehouse_prepare.add_argument("--project-id", default="placeholder-project")
    warehouse_prepare.add_argument("--dataset-prefix", default="space_launch_atlas")
    warehouse_prepare.add_argument("--location", default="replace-with-colocated-location")
    warehouse_load = commands.add_parser(
        "warehouse-load",
        help="Cloud write: load a verified snapshot from local Parquet into BigQuery",
    )
    warehouse_load.add_argument("--snapshot-id", required=True)
    warehouse_load.add_argument("--project-id", required=True)
    warehouse_load.add_argument("--dataset-prefix", default="space_launch_atlas")
    warehouse_load.add_argument("--location", required=True)
    warehouse_run = commands.add_parser(
        "warehouse-run", help="Cloud write: run load → dbt → checks; optionally activate reporting"
    )
    warehouse_run.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID"))
    warehouse_run.add_argument("--location", default=os.getenv("GCP_LOCATION"))
    warehouse_run.add_argument("--dataset-prefix", default="space_launch_atlas")
    warehouse_run.add_argument("--reporting-dataset")
    warehouse_run.add_argument("--reporting-prefix", default="")
    warehouse_run.add_argument("--publish", action="store_true")
    warehouse_run.add_argument(
        "--expected-active", help="Expected release ID, or 'none' for first activation"
    )
    warehouse_run.add_argument("--refresh-plan-id")
    warehouse_run.add_argument("--max-requests", type=int, default=2)
    rollback = commands.add_parser(
        "warehouse-rollback", help="Cloud write: select a retained verified release"
    )
    rollback.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID"))
    rollback.add_argument("--location", default=os.getenv("GCP_LOCATION"))
    rollback.add_argument("--dataset-prefix", default="space_launch_atlas")
    rollback.add_argument("--reporting-dataset")
    rollback.add_argument("--reporting-prefix", default="")
    rollback.add_argument("--release-id", required=True)
    rollback.add_argument("--expected-active", required=True)
    plan_backfill = commands.add_parser(
        "plan-backfill", help="Offline: freeze two historical passes into yearly windows"
    )
    plan_backfill.add_argument("--plan-id", required=True)
    plan_backfill.add_argument("--start-year", type=int, default=1957)
    plan_backfill.add_argument("--end-year", type=int)
    plan_refresh = commands.add_parser(
        "plan-refresh", help="Offline: freeze corrections, recent and upcoming windows"
    )
    plan_refresh.add_argument("--plan-id", required=True)
    plan_reconcile = commands.add_parser(
        "plan-reconcile", help="Offline: create another pass for unstable history windows"
    )
    plan_reconcile.add_argument("--parent-plan-id", required=True)
    plan_reconcile.add_argument("--plan-id", required=True)
    plan_runner = commands.add_parser(
        "run-plan", help="Network: resume frozen windows under one invocation request budget"
    )
    plan_runner.add_argument("--plan-id", required=True)
    plan_runner.add_argument("--max-requests", type=int, default=8)
    rebuild = commands.add_parser(
        "rebuild-state", help="Offline: rebuild SQLite and exports from retained complete raw runs"
    )
    rebuild.add_argument("--output-dir", type=Path, required=True)
    rebuild.add_argument(
        "--include-run-id",
        action="append",
        default=[],
        help="Explicitly include an offline_import run; repeat for each curated import",
    )
    import_command.add_argument(
        "--orbital-only",
        action="store_true",
        required=True,
        help="Assert provenance: include_suborbital=false was used",
    )
    for command in (
        doctor,
        *[
            commands.choices[name]
            for name in (
                "smoke",
                "backfill",
                "replay",
                "import-raw",
                "state-merge",
                "state-status",
                "state-export",
                "input-snapshot",
                "warehouse-prepare",
                "warehouse-load",
                "warehouse-run",
                "warehouse-rollback",
                "plan-backfill",
                "plan-refresh",
                "plan-reconcile",
                "run-plan",
                "rebuild-state",
            )
        ],
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
    elif arguments.command in ("run-plan", "warehouse-run"):
        overrides["max_requests"] = arguments.max_requests
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
        if arguments.command == "state-status":
            state = StateStore(settings.data_dir)
            print(
                json.dumps(
                    {
                        "revision": state.revision(),
                        "coverage_cursors": state.coverage_cursors(),
                        "plans": state.plan_summary(),
                        **state.summary(),
                    },
                    indent=2,
                )
            )
            return 0
        if arguments.command == "state-export":
            print(json.dumps(export_current(settings), indent=2))
            return 0
        if arguments.command == "input-snapshot":
            snapshot = create_input_snapshot(settings)
            print(
                json.dumps(
                    {
                        "input_snapshot_id": snapshot["input_snapshot_id"],
                        "snapshot_path": snapshot["snapshot_path"],
                        "contract_version": snapshot["contract_version"],
                        "state_revision": snapshot["state"]["revision"],
                        "export_generation_id": snapshot["export"]["generation_id"],
                        "source_runs_verified": len(snapshot["source_inputs"]),
                        "parquet_files_verified": len(snapshot["export"]["files"]),
                        "quality_passed": snapshot["export"]["quality_passed"],
                        "plans": snapshot["state"]["plans"],
                    },
                    indent=2,
                )
            )
            return 0
        if arguments.command == "warehouse-prepare":
            plan = prepare_load_plan(
                settings.data_dir,
                arguments.snapshot_id,
                CloudTarget(
                    project_id=arguments.project_id,
                    dataset_prefix=arguments.dataset_prefix,
                    location=arguments.location,
                ),
            )
            print(json.dumps(plan.summary(), indent=2))
            return 0
        if arguments.command == "warehouse-load":
            plan = prepare_load_plan(
                settings.data_dir,
                arguments.snapshot_id,
                CloudTarget(
                    project_id=arguments.project_id,
                    dataset_prefix=arguments.dataset_prefix,
                    location=arguments.location,
                ),
            )
            loader = GoogleBigQuerySourceLoader.from_application_default_credentials(
                arguments.project_id
            )
            result = execute_load_plan(plan, loader)
            print(json.dumps(result, indent=2))
            return 0
        if arguments.command in ("warehouse-run", "warehouse-rollback"):
            from google.cloud import bigquery

            from launch_atlas.warehouse.reporting import BigQueryReportingStore

            if not arguments.project_id or not arguments.location:
                raise ValueError("Provide GCP_PROJECT_ID/GCP_LOCATION or explicit project/location")
            target = CloudTarget(arguments.project_id, arguments.dataset_prefix, arguments.location)
            client = bigquery.Client(project=target.project_id)
            reporting = BigQueryReportingStore(
                client,
                target.project_id,
                arguments.reporting_dataset or target.dataset_prefix + "_reporting",
                target.location,
                candidate_dataset=target.dataset_prefix + "_scratch",
                table_prefix=arguments.reporting_prefix,
                maximum_bytes_billed=100_000_000,
            )
            if arguments.command == "warehouse-rollback":
                from launch_atlas.warehouse.releases import rollback_release

                result = rollback_release(
                    arguments.release_id, reporting, arguments.expected_active
                )
            else:
                from launch_atlas.warehouse.orchestration import execute_warehouse_pipeline
                from launch_atlas.warehouse.runner import DbtBuilder, WarehousePipeline

                if arguments.publish and arguments.expected_active is None:
                    raise ValueError(
                        "--publish requires --expected-active (use 'none' for first activation)"
                    )
                pipeline = WarehousePipeline(
                    settings,
                    target,
                    GoogleBigQuerySourceLoader(client, target.project_id),
                    DbtBuilder(target.dataset_prefix + "_scratch"),
                    reporting,
                    publish=arguments.publish,
                    expected_active=None
                    if arguments.expected_active == "none"
                    else arguments.expected_active,
                    refresh_plan_id=arguments.refresh_plan_id,
                )
                execution = execute_warehouse_pipeline(pipeline)
                if not execution.success:
                    print(json.dumps({"status": "failed", "run_dir": str(pipeline.run_dir)}))
                    return 1
                result = execution.output_for_node("activate_reporting_release")
            print(json.dumps(result, indent=2))
            return 0
        if arguments.command == "rebuild-state":
            result = rebuild_state(
                settings, arguments.output_dir, include_run_ids=set(arguments.include_run_id)
            )
            print(json.dumps(result, indent=2))
            return 0
        if arguments.command == "state-merge":
            result = merge_run_state(settings, arguments.run_id)
            print(json.dumps(result, indent=2))
            return 0
        if arguments.command == "plan-backfill":
            result = create_backfill_plan(
                StateStore(settings.data_dir),
                settings,
                arguments.plan_id,
                arguments.start_year,
                arguments.end_year or datetime.now(UTC).year,
            )
            print(json.dumps(result, indent=2))
            return 0
        if arguments.command == "plan-refresh":
            result = create_refresh_plan(StateStore(settings.data_dir), settings, arguments.plan_id)
            print(json.dumps(result, indent=2))
            return 0
        if arguments.command == "plan-reconcile":
            result = create_reconcile_plan(
                StateStore(settings.data_dir),
                settings,
                arguments.parent_plan_id,
                arguments.plan_id,
            )
            print(json.dumps(result, indent=2))
            return 0
        if arguments.command == "run-plan":
            result = run_plan(settings, arguments.plan_id)
            print(json.dumps(result, indent=2))
            return 0 if result["complete"] else 2
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
