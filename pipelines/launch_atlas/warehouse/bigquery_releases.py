"""BigQuery transactional active-release metadata backend.

Table creation is an explicit operation outside transactions. Publication and
rollback use parameterized DML in one transaction and compare the expected pointer.
"""

from __future__ import annotations

import json
import re
from typing import Any

from launch_atlas.warehouse.releases import (
    RELEASE_ID_PATTERN,
    ReleaseManifest,
    validate_release_manifest,
)

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


class BigQueryReleaseBackend:
    """Manage a metadata row and active pointer in the same BigQuery dataset."""

    def __init__(
        self,
        client: Any,
        project_id: str,
        dataset_id: str,
        location: str,
        metadata_table: str = "release_manifest",
        pointer_table: str = "active_release",
        maximum_bytes_billed: int = 1_000_000_000,
    ) -> None:
        if not PROJECT_PATTERN.fullmatch(project_id):
            raise ValueError("Invalid BigQuery project ID")
        if not IDENTIFIER_PATTERN.fullmatch(dataset_id):
            raise ValueError("Invalid BigQuery dataset ID")
        if not IDENTIFIER_PATTERN.fullmatch(metadata_table) or not IDENTIFIER_PATTERN.fullmatch(
            pointer_table
        ):
            raise ValueError("Invalid BigQuery release table ID")
        if not location.strip() or location != location.strip():
            raise ValueError("BigQuery location must be explicit")
        if maximum_bytes_billed <= 0:
            raise ValueError("maximum_bytes_billed must be positive")
        self._client = client
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.location = location
        self.metadata_table = metadata_table
        self.pointer_table = pointer_table
        self.maximum_bytes_billed = maximum_bytes_billed
        self.job_evidence: list[dict[str, Any]] = []

    @classmethod
    def from_application_default_credentials(
        cls,
        project_id: str,
        dataset_id: str,
        location: str,
        **kwargs: Any,
    ) -> BigQueryReleaseBackend:
        from google.cloud import bigquery

        return cls(bigquery.Client(project=project_id), project_id, dataset_id, location, **kwargs)

    @property
    def _metadata_ref(self) -> str:
        return f"`{self.project_id}.{self.dataset_id}.{self.metadata_table}`"

    @property
    def _pointer_ref(self) -> str:
        return f"`{self.project_id}.{self.dataset_id}.{self.pointer_table}`"

    def ensure_tables(self) -> None:
        """Create empty tables before DML; dataset default expiry remains in effect."""
        from google.api_core.exceptions import NotFound
        from google.cloud import bigquery

        definitions = (
            (
                self.metadata_table,
                [
                    bigquery.SchemaField("release_id", "STRING", mode="REQUIRED"),
                    bigquery.SchemaField("manifest_sha256", "STRING", mode="REQUIRED"),
                    bigquery.SchemaField("validated", "BOOLEAN", mode="REQUIRED"),
                    bigquery.SchemaField("manifest_json", "STRING", mode="REQUIRED"),
                    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
                ],
                "Validated release manifests retained for reporting and rollback.",
            ),
            (
                self.pointer_table,
                [
                    bigquery.SchemaField("pointer_key", "STRING", mode="REQUIRED"),
                    bigquery.SchemaField("release_id", "STRING", mode="REQUIRED"),
                    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
                ],
                "The active reporting release pointer; one row with pointer_key=active.",
            ),
        )
        for table_id, schema, description in definitions:
            table_ref = f"{self.project_id}.{self.dataset_id}.{table_id}"
            try:
                existing = self._client.get_table(table_ref)
            except NotFound:
                self._client.create_table(bigquery.Table(table_ref, schema=schema), exists_ok=True)
            else:
                actual = [(field.name, field.field_type, field.mode) for field in existing.schema]
                expected = [(field.name, field.field_type, field.mode) for field in schema]
                if actual != expected:
                    raise ValueError(f"Release table schema mismatch: {table_id}")
            table = self._client.get_table(table_ref)
            if table.description != description:
                table.description = description
                self._client.update_table(table, ["description"])

    def _query(self, sql: str, parameters: list[tuple[str, str, Any]] | None = None) -> Any:
        from google.cloud import bigquery

        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(name, field_type, value)
                for name, field_type, value in (parameters or [])
            ],
            maximum_bytes_billed=self.maximum_bytes_billed,
        )
        job = self._client.query(sql, job_config=config, location=self.location)
        rows = job.result(timeout=1800)
        self.job_evidence.append(
            {
                "job_id": job.job_id,
                "state": job.state,
                "bytes_processed": job.total_bytes_processed,
                "bytes_billed": job.total_bytes_billed,
                "maximum_bytes_billed": self.maximum_bytes_billed,
            }
        )
        return rows

    def active_release_id(self) -> str | None:
        rows = list(
            self._query(
                f"SELECT release_id FROM {self._pointer_ref} WHERE pointer_key = 'active' LIMIT 2"
            )
        )
        if len(rows) > 1:
            raise RuntimeError("BigQuery active pointer contains duplicate active rows")
        return str(rows[0].release_id) if rows else None

    def publish_validated(
        self, manifest: ReleaseManifest, expected_previous_release_id: str | None
    ) -> bool:
        if not manifest.validated or not RELEASE_ID_PATTERN.fullmatch(manifest.release_id):
            raise ValueError("Only a valid release manifest can be published")
        validate_release_manifest(manifest)
        parameters = [
            ("release_id", "STRING", manifest.release_id),
            ("manifest_sha256", "STRING", manifest.manifest_sha256),
            ("manifest_json", "STRING", json.dumps(manifest.to_dict(), sort_keys=True)),
            ("expected_previous_release_id", "STRING", expected_previous_release_id),
        ]
        rows = self._query(
            f"""DECLARE current_release_id STRING DEFAULT NULL;
DECLARE pointer_row_count INT64 DEFAULT 0;
DECLARE manifest_compatible BOOL DEFAULT FALSE;
DECLARE committed BOOL DEFAULT FALSE;
BEGIN TRANSACTION;
SET pointer_row_count = (SELECT COUNT(*) FROM {self._pointer_ref} WHERE pointer_key = 'active');
SET current_release_id = (
  SELECT ANY_VALUE(release_id) FROM {self._pointer_ref} WHERE pointer_key = 'active'
);
SET manifest_compatible = (
  SELECT COUNTIF(manifest_sha256 != @manifest_sha256) = 0
  FROM {self._metadata_ref}
  WHERE release_id = @release_id
);
IF pointer_row_count <= 1
  AND current_release_id IS NOT DISTINCT FROM @expected_previous_release_id
  AND manifest_compatible THEN
  MERGE {self._metadata_ref} AS target
  USING (SELECT @release_id AS release_id) AS source
  ON target.release_id = source.release_id
  WHEN NOT MATCHED THEN INSERT
    (release_id, manifest_sha256, validated, manifest_json, created_at)
    VALUES (@release_id, @manifest_sha256, TRUE, @manifest_json, CURRENT_TIMESTAMP());
  MERGE {self._pointer_ref} AS target
  USING (SELECT 'active' AS pointer_key, @release_id AS release_id) AS source
  ON target.pointer_key = source.pointer_key
  WHEN MATCHED THEN UPDATE SET release_id = source.release_id, updated_at = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT (pointer_key, release_id, updated_at)
    VALUES (source.pointer_key, source.release_id, CURRENT_TIMESTAMP());
  SET committed = TRUE;
END IF;
COMMIT TRANSACTION;
SELECT committed AS committed;""",
            parameters,
        )
        result = list(rows)
        return bool(result and result[0].committed)

    def retained_release(self, release_id: str) -> ReleaseManifest | None:
        rows = list(
            self._query(
                f"SELECT manifest_json FROM {self._metadata_ref} "
                "WHERE release_id = @release_id AND validated IS TRUE LIMIT 2",
                [("release_id", "STRING", release_id)],
            )
        )
        if len(rows) > 1:
            raise RuntimeError("BigQuery contains duplicate release manifest rows")
        if not rows:
            return None
        manifest = ReleaseManifest(**json.loads(rows[0].manifest_json))
        validate_release_manifest(manifest)
        return manifest

    def activate_retained(self, release_id: str, expected_current_release_id: str | None) -> bool:
        rows = self._query(
            f"""DECLARE current_release_id STRING DEFAULT NULL;
DECLARE pointer_row_count INT64 DEFAULT 0;
DECLARE target_is_validated BOOL DEFAULT FALSE;
DECLARE committed BOOL DEFAULT FALSE;
BEGIN TRANSACTION;
SET pointer_row_count = (SELECT COUNT(*) FROM {self._pointer_ref} WHERE pointer_key = 'active');
SET current_release_id = (
  SELECT ANY_VALUE(release_id) FROM {self._pointer_ref} WHERE pointer_key = 'active'
);
SET target_is_validated = (
  SELECT COUNT(*) = 1 FROM {self._metadata_ref}
  WHERE release_id = @release_id AND validated IS TRUE
);
IF pointer_row_count <= 1
  AND current_release_id IS NOT DISTINCT FROM @expected_current_release_id
  AND target_is_validated THEN
  MERGE {self._pointer_ref} AS target
  USING (SELECT 'active' AS pointer_key, @release_id AS release_id) AS source
  ON target.pointer_key = source.pointer_key
  WHEN MATCHED THEN UPDATE SET release_id = source.release_id, updated_at = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT (pointer_key, release_id, updated_at)
    VALUES (source.pointer_key, source.release_id, CURRENT_TIMESTAMP());
  SET committed = TRUE;
END IF;
COMMIT TRANSACTION;
SELECT committed AS committed;""",
            [
                ("release_id", "STRING", release_id),
                ("expected_current_release_id", "STRING", expected_current_release_id),
            ],
        )
        result = list(rows)
        return bool(result and result[0].committed)
