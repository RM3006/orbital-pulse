"""Read one validated BigQuery edition; never create or update warehouse objects."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Literal

PROJECT = "project-5584fea9-baf6-40ef-a9a"
READ_SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,100}$")
SNAPSHOT = re.compile(r"^sla-input-v1-[a-f0-9]{64}$")
RELEASE = re.compile(r"^sla-release-v1-[a-f0-9]{24}$")

# Each relation retains its own grain. No family/hardware join can multiply launches.
COLUMNS = {
    "mart_launch_attempt_daily": "launch_date_utc,launch_year_utc,provider_id,configuration_id,"
    "site_id,attempt_count,success_count,failure_count,partial_failure_count,"
    "classified_outcome_count,unresolved_count",
    "mart_family_outcomes_daily": "launch_date_utc,launch_year_utc,family_id,attempt_count,"
    "success_count,failure_count,partial_failure_count,classified_outcome_count,unresolved_count",
    "core_dim_provider": "provider_id,name",
    "core_dim_vehicle_configuration": "configuration_id,name",
    "core_dim_vehicle_family": "family_id,name",
    "core_configuration_family": "configuration_id,family_id",
    "mart_site_cadence": "launch_id,site_id,site_name,latitude,longitude,launch_date_utc,"
    "attempt_count,observed_gap_days,history_complete",
    "mart_booster_flight": "launch_id,booster_id,serial_number,launch_net_utc,source_flight_number,"
    "turnaround_days,turnaround_supported,identifiable_flight_count,reused_flight_count,"
    "reuse_known_count",
    "mart_landing": "landing_id,attempted_landing_count,known_landing_outcome_count,"
    "successful_landing_count,unknown_landing_outcome_count",
    "mart_upcoming_schedule": "launch_id,net_utc,source_status_name,scope_classification,"
    "interval_basis,schedule_earliest_utc,schedule_latest_exclusive_utc,horizon_start_utc,"
    "horizon_end_exclusive_utc",
    "mart_data_coverage": "current_launch_records,orbital_attempt_records,unknown_scope_records,"
    "unsupported_upcoming_precision_records,attempts_with_recorded_stage_flights,"
    "latest_source_update_utc,latest_observation_utc,last_complete_refresh_utc,history_complete",
}


@dataclass(frozen=True)
class AppSettings:
    project: str = PROJECT
    reporting_dataset: str = "space_launch_atlas_reporting"
    source_dataset: str = "space_launch_atlas_source"
    location: str = "europe-west1"
    maximum_bytes_billed: int = 250_000_000
    auth_mode: Literal["local", "hosted"] = "local"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", self.project):
            raise ValueError("Invalid BigQuery project")
        if not all(IDENTIFIER.fullmatch(x) for x in (self.reporting_dataset, self.source_dataset)):
            raise ValueError("Invalid dataset")
        if not 0 < self.maximum_bytes_billed <= 250_000_000:
            raise ValueError("Query cap must be between 1 and 250 MB")

    @classmethod
    def from_env(cls) -> AppSettings:
        auth_mode = os.getenv("ATLAS_AUTH_MODE", "local").strip().lower()
        if auth_mode == "local":
            mode: Literal["local", "hosted"] = "local"
        elif auth_mode == "hosted":
            mode = "hosted"
        else:
            raise ValueError("ATLAS_AUTH_MODE must be 'local' or 'hosted'")
        return cls(project=os.getenv("ATLAS_PROJECT_ID", PROJECT), auth_mode=mode)


@dataclass(frozen=True)
class Edition:
    release_id: str
    snapshot_id: str
    analysis_as_of: str
    tables: dict[str, list[dict[str, Any]]]
    activated_at: str = ""
    bytes_billed: int = 0


def edition_sql(settings: AppSettings, snapshot_id: str) -> str:
    """Only server-owned SELECT statements and allowlisted table names are possible."""
    if not SNAPSHOT.fullmatch(snapshot_id):
        raise ValueError("Invalid snapshot identity")
    root = f"{settings.project}.{settings.reporting_dataset}"
    selects = []
    for name, columns in COLUMNS.items():
        selects.append(
            f"SELECT '{name}' AS relation, TO_JSON_STRING(t) AS payload FROM "
            f"(SELECT release_id,input_snapshot_id,{columns} FROM `{root}.retained_{name}` "
            "WHERE release_id=@release AND input_snapshot_id=@snapshot) AS t"
        )
    prefix = snapshot_id.rsplit("-", 1)[-1][:24]
    labels = f"{settings.project}.{settings.source_dataset}.sla_{prefix}_launch_record_current"
    selects.append(
        "SELECT 'launch_labels' AS relation, TO_JSON_STRING(t) AS payload FROM "
        f"(SELECT input_snapshot_id,launch_id,name,provider_id,configuration_id,net_utc,"
        "observed_at_utc,is_orbital_attempt,source_status_id,source_status_name,outcome "
        f"FROM `{labels}` "
        "WHERE input_snapshot_id=@snapshot) AS t"
    )
    return "\nUNION ALL\n".join(selects)


def local_client(settings: AppSettings) -> Any:
    """Create a read-only BigQuery client using local or hosted credentials."""
    from google.cloud import bigquery

    configured = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    default_account = "sla-render-reader" if settings.auth_mode == "hosted" else "sla-app-reader"
    target = os.getenv(
        "ATLAS_READER_SERVICE_ACCOUNT",
        f"{default_account}@{settings.project}.iam.gserviceaccount.com",
    )
    if not re.fullmatch(r"[a-z][a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", target):
        raise ValueError("Invalid reader service account")

    if settings.auth_mode == "hosted":
        if not configured:
            raise ValueError("Hosted BigQuery credentials are not configured")
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            configured, scopes=[READ_SCOPE]
        )
        if credentials.service_account_email != target:
            raise ValueError("Hosted BigQuery credentials do not match the configured reader")
        return bigquery.Client(
            project=settings.project, credentials=credentials, location=settings.location
        )

    from google.auth import impersonated_credentials
    from google.oauth2.credentials import Credentials

    config_dir = os.getenv("CLOUDSDK_CONFIG")
    default_dir = (
        Path(os.environ["APPDATA"]) / "gcloud"
        if os.name == "nt"
        else Path.home() / ".config" / "gcloud"
    )
    path = (
        Path(configured)
        if configured
        else Path(config_dir or default_dir) / ("application_default_credentials.json")
    )
    source = Credentials.from_authorized_user_file(str(path))
    credentials = impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=target,
        target_scopes=[READ_SCOPE],
        lifetime=3600,
    )
    return bigquery.Client(
        project=settings.project, credentials=credentials, location=settings.location
    )


class EditionReader:
    """One immutable in-memory edition per release. No disk extracts or offline fallback."""

    def __init__(self, settings: AppSettings | None = None, client: Any = None) -> None:
        self.settings = settings or AppSettings.from_env()
        self._client = client
        self._editions: dict[str, Edition] = {}
        self._lock = Lock()

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = local_client(self.settings)
        return self._client

    def get(self, release_id: str) -> Edition:
        return self._editions[release_id]

    def latest(self) -> Edition:
        from google.cloud import bigquery

        with self._lock:
            root = f"{self.settings.project}.{self.settings.reporting_dataset}"
            # Tabledata reads incur no SQL scan, and do not follow a moving view between queries.
            pointers = [
                r
                for r in self.client.list_rows(f"{root}.active_release", max_results=3, timeout=20)
                if r.pointer_key == "active"
            ]
            if len(pointers) != 1 or not RELEASE.fullmatch(pointers[0].release_id):
                raise ValueError("A unique active reporting release is required")
            pointer = pointers[0]
            if pointer.release_id in self._editions:
                return self._editions[pointer.release_id]
            # All validated manifests can be paged; never silently take an arbitrary first 100.
            manifests = [
                json.loads(r.manifest_json)
                for r in self.client.list_rows(f"{root}.release_manifest", timeout=20)
                if r.release_id == pointer.release_id and r.validated
            ]
            if len(manifests) != 1:
                raise ValueError("Active release has no unique validated manifest")
            manifest = manifests[0]
            if manifest.get("preview") or manifest.get("validated") is not True:
                raise ValueError("Only validated production releases can be displayed")
            snapshot_id = manifest["input_snapshot_id"]
            sql = edition_sql(self.settings, snapshot_id)
            params = [
                bigquery.ScalarQueryParameter("release", "STRING", pointer.release_id),
                bigquery.ScalarQueryParameter("snapshot", "STRING", snapshot_id),
            ]
            config = bigquery.QueryJobConfig(
                maximum_bytes_billed=self.settings.maximum_bytes_billed,
                query_parameters=params,
                labels={"application": "printed-atlas", "purpose": "report-reader"},
            )
            dry = self.client.query(
                sql,
                api_method=bigquery.enums.QueryApiMethod.QUERY,
                job_config=bigquery.QueryJobConfig(
                    dry_run=True, use_query_cache=False, query_parameters=params
                ),
                timeout=20,
            )
            # jobs.query dry-run replies may omit statementType. The query builder is
            # server-owned SELECT-only and the token itself has bigquery.readonly scope.
            if dry.statement_type not in {None, "SELECT"}:
                raise ValueError("The reporting reader accepts SELECT queries only")
            if (dry.total_bytes_processed or 0) > self.settings.maximum_bytes_billed:
                raise ValueError("Edition exceeds the configured query allowance")
            job = self.client.query(
                sql, job_config=config, timeout=20, api_method=bigquery.enums.QueryApiMethod.QUERY
            )
            tables: dict[str, list[dict[str, Any]]] = {name: [] for name in COLUMNS}
            tables["launch_labels"] = []
            for row in job.result(timeout=120):
                value = json.loads(row.payload)
                if value.get("input_snapshot_id") != snapshot_id:
                    raise ValueError("Mixed snapshot rows in edition")
                if (
                    row.relation != "launch_labels"
                    and value.get("release_id") != pointer.release_id
                ):
                    raise ValueError("Mixed reporting releases in edition")
                tables[row.relation].append(value)
            for name in COLUMNS:
                if len(tables[name]) != manifest["reporting_row_counts"][name]:
                    raise ValueError(f"Retained row count changed for {name}")
            if len(tables["mart_data_coverage"]) != 1:
                raise ValueError("An edition needs exactly one coverage record")
            labels = tables["launch_labels"]
            if len({r["launch_id"] for r in labels}) != len(labels):
                raise ValueError("Launch label keys are not unique")
            if len(labels) != tables["mart_data_coverage"][0]["current_launch_records"]:
                raise ValueError("Launch labels do not cover the selected source snapshot")
            label_ids = {r["launch_id"] for r in labels}
            for relation in ("mart_upcoming_schedule", "mart_booster_flight"):
                if any(r["launch_id"] not in label_ids for r in tables[relation]):
                    raise ValueError(f"Snapshot labels are missing for {relation}")
            edition = Edition(
                pointer.release_id,
                snapshot_id,
                manifest["analysis_as_of_utc"],
                tables,
                str(pointer.updated_at),
                int(job.total_bytes_billed or 0),
            )
            # Keep a bounded cache; existing sessions retain their edition until a full reload.
            if len(self._editions) >= 8:
                raise ValueError("Restart the local app to load additional reporting editions")
            self._editions[edition.release_id] = edition
            return edition
