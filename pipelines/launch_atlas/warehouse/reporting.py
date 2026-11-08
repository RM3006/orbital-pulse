"""Retain reporting rows before atomically selecting a validated release.

Views reference retained tables and one pointer, never mutable dbt relations.
All DDL runs outside the metadata transaction. Incomplete copies remain invisible.
"""

from __future__ import annotations

from typing import Any

from launch_atlas.warehouse.bigquery_releases import (
    IDENTIFIER_PATTERN,
    BigQueryReleaseBackend,
)
from launch_atlas.warehouse.loading import SNAPSHOT_ID_PATTERN, _canonical_hash
from launch_atlas.warehouse.releases import (
    REQUIRED_REPORTING_TABLES,
    CandidateRejected,
    ReleaseManifest,
    validate_release_manifest,
)

MANAGED_COLUMNS = {"release_id", "reporting_analysis_as_of_utc"}


def schema_identity(fields: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": f.name,
            "type": f.field_type,
            "mode": f.mode,
            "fields": schema_identity(list(f.fields)),
        }
        for f in fields
    ]


class BigQueryReportingStore(BigQueryReleaseBackend):
    """Release backend that also verifies the rows needed for publication/rollback."""

    def __init__(
        self,
        client: Any,
        project_id: str,
        dataset_id: str,
        location: str,
        *,
        candidate_dataset: str,
        table_prefix: str = "",
        maximum_bytes_billed: int = 1_000_000_000,
    ) -> None:
        if not IDENTIFIER_PATTERN.fullmatch(candidate_dataset):
            raise ValueError("Invalid candidate dataset")
        if table_prefix and (
            not IDENTIFIER_PATTERN.fullmatch(table_prefix) or len(table_prefix) > 64
        ):
            raise ValueError("Invalid reporting table prefix")
        super().__init__(
            client,
            project_id,
            dataset_id,
            location,
            metadata_table=table_prefix + "release_manifest",
            pointer_table=table_prefix + "active_release",
            maximum_bytes_billed=maximum_bytes_billed,
        )
        self.candidate_dataset = candidate_dataset
        self.table_prefix = table_prefix

    def _retained(self, name: str) -> str:
        if name not in REQUIRED_REPORTING_TABLES:
            raise ValueError("Unknown reporting table")
        return f"{self.project_id}.{self.dataset_id}.{self.table_prefix}retained_{name}"

    def _candidate(self, relation: str) -> str:
        parts = relation.split(".")
        if (
            len(parts) != 3
            or parts[0] != self.project_id
            or parts[1] != self.candidate_dataset
            or not IDENTIFIER_PATTERN.fullmatch(parts[2])
        ):
            raise ValueError("Candidate relation is outside the configured scratch dataset")
        return relation

    def _inspect(
        self, relation: str, snapshot_id: str, release_id: str | None = None
    ) -> tuple[int, str]:
        if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
            raise ValueError("Invalid snapshot ID")
        table = self._client.get_table(relation)
        fields = [field for field in table.schema if field.name not in MANAGED_COLUMNS]
        if not any(field.name == "input_snapshot_id" for field in fields):
            raise CandidateRejected("Reporting relation has no input snapshot key")
        columns = ", ".join(f"`{field.name}`" for field in fields)
        where = " WHERE release_id = @release_id" if release_id else ""
        params = [("snapshot_id", "STRING", snapshot_id)]
        if release_id:
            params.append(("release_id", "STRING", release_id))
        row = list(
            self._query(
                "WITH hashes AS (SELECT TO_HEX(SHA256(TO_JSON_STRING(t))) AS row_hash, "
                "input_snapshot_id IS DISTINCT FROM @snapshot_id AS other_snapshot "
                f"FROM (SELECT {columns} FROM `{relation}`{where}) AS t), "
                "buckets AS (SELECT SUBSTR(row_hash, 1, 2) AS bucket, COUNT(*) AS n, "
                "COUNTIF(other_snapshot) AS bad, "
                "TO_HEX(SHA256(STRING_AGG(row_hash, '' ORDER BY row_hash))) AS bucket_hash "
                "FROM hashes GROUP BY bucket) "
                "SELECT COALESCE(SUM(n), 0) AS row_count, "
                "COALESCE(SUM(bad), 0) AS other_snapshot_rows, "
                "TO_HEX(SHA256(COALESCE(STRING_AGG(CONCAT(bucket, ':', CAST(n AS STRING), ':', "
                "bucket_hash), '|' ORDER BY bucket), ''))) AS content_hash FROM buckets",
                params,
            )
        )[0]
        if row.other_snapshot_rows:
            raise CandidateRejected("Reporting data contains another input snapshot")
        count = int(row.row_count)
        signature = _canonical_hash(
            {
                "schema": schema_identity(fields),
                "rows": count,
                "row_multiset_sha256": str(row.content_hash),
            }
        )
        return count, signature

    def inspect_candidate(
        self, relations: dict[str, str], snapshot_id: str
    ) -> tuple[dict[str, int], dict[str, str]]:
        if set(relations) != REQUIRED_REPORTING_TABLES:
            raise CandidateRejected("Candidate does not expose all reporting relations")
        counts, signatures = {}, {}
        for name, relation in sorted(relations.items()):
            counts[name], signatures[name] = self._inspect(self._candidate(relation), snapshot_id)
        return counts, signatures

    def retain_candidate(self, manifest: ReleaseManifest, relations: dict[str, str]) -> None:
        from google.cloud import bigquery

        counts, signatures = self.inspect_candidate(relations, manifest.input_snapshot_id)
        if (
            counts != manifest.reporting_row_counts
            or signatures != manifest.reporting_content_sha256
        ):
            raise CandidateRejected("Candidate changed after validation")
        self.ensure_tables()
        for name, relation in sorted(relations.items()):
            source = self._client.get_table(self._candidate(relation))
            if MANAGED_COLUMNS.intersection(field.name for field in source.schema):
                raise CandidateRejected("Candidate contains release-managed columns")
            retained = self._retained(name)
            schema = [
                bigquery.SchemaField("release_id", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("reporting_analysis_as_of_utc", "TIMESTAMP", mode="REQUIRED"),
                *source.schema,
            ]
            self._client.create_table(bigquery.Table(retained, schema=schema), exists_ok=True)
            actual = self._client.get_table(retained)
            if schema_identity(list(actual.schema)) != schema_identity(schema):
                raise CandidateRejected(f"Retained reporting schema changed: {name}")
            self._query(
                f"INSERT INTO `{retained}` SELECT @release_id, TIMESTAMP(@as_of), src.* "
                f"FROM `{relation}` AS src WHERE NOT EXISTS "
                f"(SELECT 1 FROM `{retained}` WHERE release_id = @release_id)",
                [
                    ("release_id", "STRING", manifest.release_id),
                    ("as_of", "STRING", manifest.analysis_as_of_utc),
                ],
            )
            view_id = f"{self.project_id}.{self.dataset_id}.{self.table_prefix}{name}"
            view_sql = (
                "SELECT retained_rows.*, manifest.created_at AS reporting_published_at_utc, "
                "active.updated_at AS reporting_activated_at_utc "
                f"FROM `{retained}` AS retained_rows "
                f"JOIN {self._pointer_ref} AS active "
                "ON retained_rows.release_id = active.release_id "
                f"JOIN {self._metadata_ref} AS manifest "
                "ON retained_rows.release_id = manifest.release_id "
                "WHERE active.pointer_key = 'active' AND manifest.validated IS TRUE"
            )
            view = bigquery.Table(view_id)
            view.view_query = view_sql
            self._client.create_table(view, exists_ok=True)
            if self._client.get_table(view_id).view_query != view_sql:
                raise CandidateRejected(f"Reporting view definition changed: {name}")
        self.verify_retained(manifest)

    def verify_retained(self, manifest: ReleaseManifest) -> None:
        validate_release_manifest(manifest)
        if set(manifest.reporting_row_counts) != REQUIRED_REPORTING_TABLES:
            raise CandidateRejected("Retained manifest has incomplete reporting tables")
        for name in sorted(REQUIRED_REPORTING_TABLES):
            count, signature = self._inspect(
                self._retained(name), manifest.input_snapshot_id, manifest.release_id
            )
            if count != manifest.reporting_row_counts[
                name
            ] or signature != manifest.reporting_content_sha256.get(name):
                raise CandidateRejected(f"Retained reporting data changed or is missing: {name}")

    def publish_validated(
        self, manifest: ReleaseManifest, expected_previous_release_id: str | None
    ) -> bool:
        self.verify_retained(manifest)
        return super().publish_validated(manifest, expected_previous_release_id)

    def retained_release(self, release_id: str) -> ReleaseManifest | None:
        manifest = super().retained_release(release_id)
        if manifest:
            self.verify_retained(manifest)
        return manifest

    def activate_retained(self, release_id: str, expected_current_release_id: str | None) -> bool:
        if self.retained_release(release_id) is None:
            raise CandidateRejected("Rollback target is missing")
        return super().activate_retained(release_id, expected_current_release_id)
