"""Transactional local state for accepted LL2 acquisitions and change history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from launch_atlas.normalize import PARSER_VERSION, timestamp
from launch_atlas.observations import HASH_VERSION, observation_event_key, semantic_hash
from launch_atlas.time_precision import schedule_interval

SCHEMA_VERSION = 4


def _utc(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("State timestamps must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


class StateStore:
    """One local SQLite database; source JSON and manifests remain immutable elsewhere."""

    def __init__(self, data_dir: Path):
        self.path = data_dir.resolve() / "state" / "launch_atlas.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self.session() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError(f"State schema {version} is newer than supported {SCHEMA_VERSION}")
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE acquisition (
                        acquisition_id TEXT PRIMARY KEY,
                        observed_at_utc TEXT NOT NULL,
                        source_run_id TEXT NOT NULL,
                        applied_at_utc TEXT NOT NULL
                    );
                    CREATE TABLE semantic_version (
                        launch_id TEXT NOT NULL,
                        hash_version TEXT NOT NULL,
                        record_hash TEXT NOT NULL,
                        first_seen_at_utc TEXT NOT NULL,
                        PRIMARY KEY (launch_id, hash_version, record_hash)
                    );
                    CREATE TABLE record_sighting (
                        acquisition_id TEXT NOT NULL REFERENCES acquisition(acquisition_id),
                        record_ordinal INTEGER NOT NULL,
                        launch_id TEXT NOT NULL,
                        hash_version TEXT NOT NULL,
                        record_hash TEXT NOT NULL,
                        source_updated_at_utc TEXT NOT NULL,
                        observed_at_utc TEXT NOT NULL,
                        raw_artifact TEXT,
                        raw_record_json TEXT NOT NULL,
                        disposition TEXT NOT NULL,
                        PRIMARY KEY (acquisition_id, record_ordinal),
                        FOREIGN KEY (launch_id, hash_version, record_hash)
                            REFERENCES semantic_version(launch_id, hash_version, record_hash)
                    );
                    CREATE INDEX sighting_launch_time
                        ON record_sighting(launch_id, observed_at_utc);
                    CREATE TABLE observation_event (
                        observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        acquisition_id TEXT NOT NULL,
                        record_ordinal INTEGER NOT NULL,
                        launch_id TEXT NOT NULL,
                        hash_version TEXT NOT NULL,
                        record_hash TEXT NOT NULL,
                        source_updated_at_utc TEXT NOT NULL,
                        observed_at_utc TEXT NOT NULL,
                        event_kind TEXT NOT NULL,
                        FOREIGN KEY (acquisition_id, record_ordinal)
                            REFERENCES record_sighting(acquisition_id, record_ordinal),
                        FOREIGN KEY (launch_id, hash_version, record_hash)
                            REFERENCES semantic_version(launch_id, hash_version, record_hash)
                    );
                    CREATE TABLE launch_current (
                        launch_id TEXT PRIMARY KEY,
                        hash_version TEXT NOT NULL,
                        record_hash TEXT NOT NULL,
                        source_updated_at_utc TEXT NOT NULL,
                        first_seen_at_utc TEXT NOT NULL,
                        last_seen_at_utc TEXT NOT NULL,
                        raw_artifact TEXT,
                        raw_record_json TEXT NOT NULL,
                        review_required INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (launch_id, hash_version, record_hash)
                            REFERENCES semantic_version(launch_id, hash_version, record_hash)
                    );
                    CREATE TABLE state_conflict (
                        acquisition_id TEXT NOT NULL,
                        record_ordinal INTEGER NOT NULL,
                        launch_id TEXT NOT NULL,
                        existing_hash TEXT NOT NULL,
                        incoming_hash TEXT NOT NULL,
                        source_updated_at_utc TEXT NOT NULL,
                        detected_at_utc TEXT NOT NULL,
                        PRIMARY KEY (acquisition_id, record_ordinal),
                        FOREIGN KEY (acquisition_id, record_ordinal)
                            REFERENCES record_sighting(acquisition_id, record_ordinal)
                    );
                    PRAGMA user_version = 1;
                    """
                )
                version = 1
            if version == 1:
                connection.executescript(
                    """
                    CREATE TABLE state_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    INSERT INTO state_meta VALUES ('revision', '0');
                    CREATE TABLE extraction_plan (
                        plan_id TEXT PRIMARY KEY,
                        plan_type TEXT NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        frozen_as_of_utc TEXT NOT NULL,
                        config_json TEXT NOT NULL,
                        status TEXT NOT NULL
                    );
                    CREATE TABLE extraction_window (
                        window_id TEXT PRIMARY KEY,
                        plan_id TEXT NOT NULL REFERENCES extraction_plan(plan_id),
                        parent_window_id TEXT REFERENCES extraction_window(window_id),
                        stream_id TEXT NOT NULL,
                        start_utc TEXT NOT NULL,
                        end_utc TEXT NOT NULL,
                        query_json TEXT NOT NULL,
                        pass_number INTEGER NOT NULL DEFAULT 1,
                        next_url TEXT,
                        extraction_status TEXT NOT NULL DEFAULT 'planned',
                        validation_passed INTEGER,
                        state_revision INTEGER,
                        source_count INTEGER,
                        fetched_unique_count INTEGER,
                        UNIQUE (plan_id, stream_id, start_utc, end_utc, pass_number)
                    );
                    CREATE TABLE raw_request (
                        acquisition_id TEXT NOT NULL REFERENCES acquisition(acquisition_id),
                        request_ordinal INTEGER NOT NULL,
                        requested_url TEXT NOT NULL,
                        requested_at_utc TEXT NOT NULL,
                        response_received_at_utc TEXT,
                        http_status INTEGER,
                        artifact TEXT,
                        sha256 TEXT,
                        transport_error TEXT,
                        PRIMARY KEY (acquisition_id, request_ordinal)
                    );
                    PRAGMA user_version = 2;
                    """
                )
                version = 2
            if version == 2:
                connection.executescript(
                    """
                    CREATE TABLE coverage_cursor (
                        stream_id TEXT PRIMARY KEY,
                        committed_end_utc TEXT NOT NULL,
                        window_id TEXT NOT NULL REFERENCES extraction_window(window_id)
                    );
                    PRAGMA user_version = 3;
                    """
                )
                version = 3
            if version == 3:
                connection.executescript(
                    """
                    ALTER TABLE extraction_window ADD COLUMN content_digest TEXT;
                    ALTER TABLE extraction_window ADD COLUMN reconciled INTEGER NOT NULL DEFAULT 0;
                    PRAGMA user_version = 4;
                    """
                )

    def apply_acquisition(
        self,
        acquisition_id: str,
        source_run_id: str,
        records: list[tuple[dict[str, Any], dict[str, Any]]],
        *,
        window_id: str | None = None,
        request_evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Apply one replayable raw acquisition exactly once in one state transaction."""
        if not acquisition_id or not source_run_id:
            raise ValueError("Acquisition and source run IDs are required")
        if not records:
            raise ValueError("Refusing to apply an empty acquisition without page evidence")
        observed_values = [_utc(str(envelope["observed_at_utc"])) for _, envelope in records]
        observed_at = max(observed_values)
        counts = {key: 0 for key in ("new", "changed", "unchanged", "stale", "conflict")}
        now = datetime.now(UTC).isoformat()
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT observed_at_utc FROM acquisition WHERE acquisition_id = ?",
                (acquisition_id,),
            ).fetchone()
            if previous:
                return {"acquisition_id": acquisition_id, "already_applied": True, **counts}
            connection.execute(
                "INSERT INTO acquisition VALUES (?, ?, ?, ?)",
                (acquisition_id, observed_at, source_run_id, now),
            )
            for ordinal, request in enumerate(request_evidence or [], start=1):
                requested_at = request.get("request_started_at_utc")
                if requested_at is None:
                    # Older Step 1 manifests have receipt time only; do not invent send time.
                    continue
                connection.execute(
                    """INSERT INTO raw_request
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        acquisition_id,
                        ordinal,
                        request["url"],
                        _utc(requested_at),
                        _utc(request["observed_at_utc"]),
                        request.get("http_status"),
                        request.get("artifact"),
                        request.get("sha256"),
                        "transport_error" if request.get("http_status") is None else None,
                    ),
                )
            window = None
            if window_id is not None:
                window = connection.execute(
                    "SELECT * FROM extraction_window WHERE window_id=?", (window_id,)
                ).fetchone()
                if window is None or window["validation_passed"] != 1:
                    raise ValueError("Only a validated extraction window can commit")
            for ordinal, (record, envelope) in enumerate(records):
                record_observed_at = _utc(str(envelope["observed_at_utc"]))
                launch_id = str(record["id"])
                source_updated = _utc(str(record["last_updated"]))
                record_hash = semantic_hash(record)
                raw_json = json.dumps(record, sort_keys=True, separators=(",", ":"))
                raw_artifact = envelope.get("artifact")
                connection.execute(
                    """INSERT OR IGNORE INTO semantic_version
                       VALUES (?, ?, ?, ?)""",
                    (launch_id, HASH_VERSION, record_hash, record_observed_at),
                )
                current = connection.execute(
                    "SELECT * FROM launch_current WHERE launch_id = ?", (launch_id,)
                ).fetchone()
                disposition = "new"
                event_kind = "first_observed"
                if current:
                    current_hash = current["record_hash"]
                    current_updated = current["source_updated_at_utc"]
                    if source_updated < current_updated:
                        disposition = "stale"
                        event_kind = ""
                    elif source_updated == current_updated and record_hash != current_hash:
                        disposition = "conflict"
                        event_kind = ""
                    elif record_hash == current_hash:
                        disposition = "unchanged"
                        event_kind = ""
                    else:
                        disposition = "changed"
                        event_kind = "transition"
                counts[disposition] += 1
                connection.execute(
                    """INSERT INTO record_sighting
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        acquisition_id,
                        ordinal,
                        launch_id,
                        HASH_VERSION,
                        record_hash,
                        source_updated,
                        record_observed_at,
                        raw_artifact,
                        raw_json,
                        disposition,
                    ),
                )
                if disposition == "conflict":
                    connection.execute(
                        """INSERT INTO state_conflict VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            acquisition_id,
                            ordinal,
                            launch_id,
                            current["record_hash"],
                            record_hash,
                            source_updated,
                            record_observed_at,
                        ),
                    )
                elif disposition in ("new", "changed"):
                    connection.execute(
                        """INSERT INTO observation_event
                           (acquisition_id, record_ordinal, launch_id, hash_version,
                            record_hash, source_updated_at_utc, observed_at_utc, event_kind)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            acquisition_id,
                            ordinal,
                            launch_id,
                            HASH_VERSION,
                            record_hash,
                            source_updated,
                            record_observed_at,
                            event_kind,
                        ),
                    )
                    if current:
                        connection.execute(
                            """UPDATE launch_current SET hash_version=?, record_hash=?,
                               source_updated_at_utc=?, last_seen_at_utc=?, raw_artifact=?,
                               raw_record_json=?, review_required=0 WHERE launch_id=?""",
                            (
                                HASH_VERSION,
                                record_hash,
                                source_updated,
                                record_observed_at,
                                raw_artifact,
                                raw_json,
                                launch_id,
                            ),
                        )
                    else:
                        connection.execute(
                            """INSERT INTO launch_current
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                            (
                                launch_id,
                                HASH_VERSION,
                                record_hash,
                                source_updated,
                                record_observed_at,
                                record_observed_at,
                                raw_artifact,
                                raw_json,
                            ),
                        )
                elif disposition == "unchanged":
                    connection.execute(
                        "UPDATE launch_current SET last_seen_at_utc=? WHERE launch_id=?",
                        (record_observed_at, launch_id),
                    )
            connection.execute(
                "UPDATE state_meta SET value = CAST(value AS INTEGER) + 1 WHERE key='revision'"
            )
            if window is not None:
                identities = sorted(
                    {(str(record["id"]), semantic_hash(record)) for record, _ in records}
                )
                content_digest = hashlib.sha256(
                    json.dumps(identities, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if window["parent_window_id"]:
                    previous_pass = connection.execute(
                        "SELECT content_digest FROM extraction_window "
                        "WHERE window_id=? AND extraction_status='committed'",
                        (window["parent_window_id"],),
                    ).fetchone()
                else:
                    previous_pass = connection.execute(
                        """SELECT content_digest FROM extraction_window
                           WHERE plan_id=? AND stream_id=? AND start_utc=? AND end_utc=?
                             AND pass_number=? AND extraction_status='committed'""",
                        (
                            window["plan_id"],
                            window["stream_id"],
                            window["start_utc"],
                            window["end_utc"],
                            window["pass_number"] - 1,
                        ),
                    ).fetchone()
                reconciled = bool(
                    previous_pass and previous_pass["content_digest"] == content_digest
                )
                revision = int(
                    connection.execute(
                        "SELECT value FROM state_meta WHERE key='revision'"
                    ).fetchone()[0]
                )
                connection.execute(
                    """UPDATE extraction_window SET extraction_status='committed',
                       state_revision=?, content_digest=?, reconciled=? WHERE window_id=?""",
                    (revision, content_digest, int(reconciled), window_id),
                )
                connection.execute(
                    """INSERT INTO coverage_cursor VALUES (?, ?, ?)
                       ON CONFLICT(stream_id) DO UPDATE SET
                       committed_end_utc=excluded.committed_end_utc,
                       window_id=excluded.window_id
                       WHERE excluded.committed_end_utc > coverage_cursor.committed_end_utc""",
                    (window["stream_id"], window["end_utc"], window_id),
                )
            return {"acquisition_id": acquisition_id, "already_applied": False, **counts}

    def create_plan(
        self,
        plan_id: str,
        plan_type: str,
        frozen_as_of_utc: str,
        config: dict[str, Any],
        windows: list[dict[str, Any]],
    ) -> None:
        """Create an immutable plan and its query windows, or verify an exact retry."""
        frozen_as_of = _utc(frozen_as_of_utc)
        config_json = json.dumps(config, sort_keys=True, separators=(",", ":"))
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT * FROM extraction_plan WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if prior:
                if (
                    prior["plan_type"] != plan_type
                    or prior["frozen_as_of_utc"] != frozen_as_of
                    or prior["config_json"] != config_json
                ):
                    raise ValueError("Plan ID already exists with different frozen parameters")
                existing = connection.execute(
                    """SELECT window_id, parent_window_id, stream_id, start_utc,
                       end_utc, query_json, pass_number FROM extraction_window
                       WHERE plan_id=? ORDER BY window_id""",
                    (plan_id,),
                ).fetchall()
                proposed = sorted(
                    (
                        window["window_id"],
                        window.get("parent_window_id"),
                        window["stream_id"],
                        _utc(window["start_utc"]),
                        _utc(window["end_utc"]),
                        json.dumps(window["query"], sort_keys=True, separators=(",", ":")),
                        int(window.get("pass_number", 1)),
                    )
                    for window in windows
                )
                if [tuple(row) for row in existing] != proposed:
                    raise ValueError(
                        "Plan ID already exists with a different extraction window set"
                    )
                return
            connection.execute(
                "INSERT INTO extraction_plan VALUES (?, ?, ?, ?, ?, 'planned')",
                (plan_id, plan_type, datetime.now(UTC).isoformat(), frozen_as_of, config_json),
            )
            for window in windows:
                query_json = json.dumps(window["query"], sort_keys=True, separators=(",", ":"))
                connection.execute(
                    """INSERT INTO extraction_window
                       (window_id, plan_id, parent_window_id, stream_id, start_utc,
                        end_utc, query_json, pass_number, next_url, extraction_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned')""",
                    (
                        window["window_id"],
                        plan_id,
                        window.get("parent_window_id"),
                        window["stream_id"],
                        _utc(window["start_utc"]),
                        _utc(window["end_utc"]),
                        query_json,
                        int(window.get("pass_number", 1)),
                        window.get("initial_url"),
                    ),
                )

    def window(self, window_id: str) -> dict[str, Any]:
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM extraction_window WHERE window_id=?", (window_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown extraction window {window_id}")
            return dict(row)

    def coverage_cursors(self) -> dict[str, str]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT stream_id, committed_end_utc FROM coverage_cursor ORDER BY stream_id"
            ).fetchall()
            return {row["stream_id"]: row["committed_end_utc"] for row in rows}

    def plan_summary(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            plans = connection.execute(
                "SELECT * FROM extraction_plan ORDER BY created_at_utc"
            ).fetchall()
            result = []
            for plan in plans:
                windows = connection.execute(
                    """SELECT extraction_status, COUNT(*) AS count
                       FROM extraction_window WHERE plan_id=? GROUP BY extraction_status""",
                    (plan["plan_id"],),
                ).fetchall()
                result.append(
                    {
                        "plan_id": plan["plan_id"],
                        "plan_type": plan["plan_type"],
                        "frozen_as_of_utc": plan["frozen_as_of_utc"],
                        "status": plan["status"],
                        "windows": {row["extraction_status"]: row["count"] for row in windows},
                    }
                )
            return result

    def plan_windows(self, plan_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT * FROM extraction_window WHERE plan_id=?
                   ORDER BY pass_number, start_utc, window_id""",
                (plan_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def unreconciled_windows(self, plan_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT candidate.* FROM extraction_window AS candidate
                   WHERE candidate.plan_id=? AND candidate.extraction_status='committed'
                     AND candidate.pass_number=(
                       SELECT MAX(latest.pass_number) FROM extraction_window AS latest
                       WHERE latest.plan_id=candidate.plan_id
                         AND latest.stream_id=candidate.stream_id
                         AND latest.start_utc=candidate.start_utc
                         AND latest.end_utc=candidate.end_utc)
                     AND candidate.reconciled=0
                     AND EXISTS (
                       SELECT 1 FROM extraction_window AS prior
                       WHERE ((candidate.parent_window_id IS NOT NULL
                               AND prior.window_id=candidate.parent_window_id)
                          OR (candidate.parent_window_id IS NULL
                              AND prior.plan_id=candidate.plan_id
                              AND prior.stream_id=candidate.stream_id
                              AND prior.start_utc=candidate.start_utc
                              AND prior.end_utc=candidate.end_utc
                              AND prior.pass_number=candidate.pass_number-1))
                         AND prior.extraction_status='committed')
                   ORDER BY candidate.start_utc""",
                (plan_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def plan(self, plan_id: str) -> dict[str, Any]:
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM extraction_plan WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown plan {plan_id}")
            result = dict(row)
            result["config"] = json.loads(result.pop("config_json"))
            return result

    def set_plan_status(self, plan_id: str, status: str) -> None:
        if status not in ("planned", "in_progress", "reconciliation_required", "complete"):
            raise ValueError("Invalid plan status")
        with self.session() as connection:
            cursor = connection.execute(
                "UPDATE extraction_plan SET status=? WHERE plan_id=?", (status, plan_id)
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Unknown plan {plan_id}")

    def latest_complete_refresh_asof(self) -> datetime | None:
        with self.session() as connection:
            meta = connection.execute(
                "SELECT value FROM state_meta WHERE key='latest_refresh_as_of_utc'"
            ).fetchone()
            if meta:
                return datetime.fromisoformat(meta[0])
            row = connection.execute(
                """SELECT frozen_as_of_utc FROM extraction_plan
                   WHERE plan_type='refresh' AND status='complete'
                   ORDER BY frozen_as_of_utc DESC LIMIT 1"""
            ).fetchone()
            return datetime.fromisoformat(row[0]) if row else None

    def set_latest_refresh_asof(self, as_of_utc: str) -> None:
        as_of = _utc(as_of_utc)
        with self.session() as connection:
            connection.execute(
                """INSERT INTO state_meta VALUES ('latest_refresh_as_of_utc', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (as_of,),
            )

    def checkpoint_window(self, window_id: str, next_url: str | None) -> None:
        with self.session() as connection:
            cursor = connection.execute(
                """UPDATE extraction_window SET next_url=?, extraction_status='extracting'
                   WHERE window_id=? AND extraction_status IN ('planned', 'extracting')""",
                (next_url, window_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Window cannot be checkpointed in its current state")

    def split_window(self, window_id: str, children: list[dict[str, Any]]) -> None:
        """Replace an uncommitted annual extraction window with immutable child windows."""
        if not children:
            raise ValueError("A split must create at least one child window")
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            parent = connection.execute(
                "SELECT * FROM extraction_window WHERE window_id=?", (window_id,)
            ).fetchone()
            if parent is None or parent["extraction_status"] == "committed":
                raise ValueError("A missing or committed window cannot be split")
            siblings = connection.execute(
                """SELECT window_id, extraction_status FROM extraction_window
                   WHERE plan_id=? AND stream_id=? AND start_utc=? AND end_utc=?""",
                (parent["plan_id"], parent["stream_id"], parent["start_utc"], parent["end_utc"]),
            ).fetchall()
            if any(row["extraction_status"] == "committed" for row in siblings):
                raise ValueError("A partition cannot be split after any pass committed")
            connection.executemany(
                "UPDATE extraction_window SET extraction_status='split' WHERE window_id=?",
                [(row["window_id"],) for row in siblings],
            )
            for child in children:
                connection.execute(
                    """INSERT INTO extraction_window
                       (window_id, plan_id, parent_window_id, stream_id, start_utc, end_utc,
                        query_json, pass_number, next_url, extraction_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned')""",
                    (
                        child["window_id"],
                        parent["plan_id"],
                        window_id,
                        child["stream_id"],
                        _utc(child["start_utc"]),
                        _utc(child["end_utc"]),
                        json.dumps(child["query"], sort_keys=True, separators=(",", ":")),
                        int(child["pass_number"]),
                        child["initial_url"],
                    ),
                )

    def validate_window(
        self, window_id: str, passed: bool, source_count: int, unique_count: int
    ) -> None:
        with self.session() as connection:
            cursor = connection.execute(
                """UPDATE extraction_window SET extraction_status=?, validation_passed=?,
                   source_count=?, fetched_unique_count=?
                   WHERE window_id=? AND extraction_status='extracted'""",
                (
                    "validated" if passed else "validation_failed",
                    int(passed),
                    source_count,
                    unique_count,
                    window_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Window cannot be validated in its current state")

    def mark_window_extracted(self, window_id: str) -> None:
        with self.session() as connection:
            cursor = connection.execute(
                """UPDATE extraction_window SET extraction_status='extracted'
                   WHERE window_id=? AND extraction_status='extracting' AND next_url IS NULL""",
                (window_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Window has a pending page or is not extracting")

    def commit_empty_window(
        self,
        window_id: str,
        *,
        acquisition_id: str | None = None,
        source_run_id: str | None = None,
        observed_at_utc: str | None = None,
    ) -> None:
        """Commit a validated empty window and cursor atomically, preserving valid zero coverage."""
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM extraction_window WHERE window_id=?", (window_id,)
            ).fetchone()
            if row is None or row["validation_passed"] != 1 or row["fetched_unique_count"] != 0:
                raise ValueError("Only a validated empty window may advance an empty cursor")
            if acquisition_id is not None:
                if not source_run_id or not observed_at_utc:
                    raise ValueError("Empty acquisition evidence requires run ID and receipt time")
                connection.execute(
                    "INSERT OR IGNORE INTO acquisition VALUES (?, ?, ?, ?)",
                    (
                        acquisition_id,
                        _utc(observed_at_utc),
                        source_run_id,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            revision = (
                int(
                    connection.execute(
                        "SELECT value FROM state_meta WHERE key='revision'"
                    ).fetchone()[0]
                )
                + 1
            )
            connection.execute(
                "UPDATE state_meta SET value=? WHERE key='revision'", (str(revision),)
            )
            connection.execute(
                "UPDATE extraction_window SET extraction_status='committed', "
                "state_revision=?, content_digest=? WHERE window_id=?",
                (revision, hashlib.sha256(b"[]").hexdigest(), window_id),
            )
            if row["parent_window_id"]:
                previous_pass = connection.execute(
                    "SELECT content_digest FROM extraction_window "
                    "WHERE window_id=? AND extraction_status='committed'",
                    (row["parent_window_id"],),
                ).fetchone()
            else:
                previous_pass = connection.execute(
                    """SELECT content_digest FROM extraction_window
                       WHERE plan_id=? AND stream_id=? AND start_utc=? AND end_utc=?
                         AND pass_number=? AND extraction_status='committed'""",
                    (
                        row["plan_id"],
                        row["stream_id"],
                        row["start_utc"],
                        row["end_utc"],
                        row["pass_number"] - 1,
                    ),
                ).fetchone()
            if (
                previous_pass
                and previous_pass["content_digest"] == hashlib.sha256(b"[]").hexdigest()
            ):
                connection.execute(
                    "UPDATE extraction_window SET reconciled=1 WHERE window_id=?", (window_id,)
                )
            connection.execute(
                """INSERT INTO coverage_cursor VALUES (?, ?, ?)
                   ON CONFLICT(stream_id) DO UPDATE SET
                   committed_end_utc=excluded.committed_end_utc,
                   window_id=excluded.window_id
                   WHERE excluded.committed_end_utc > coverage_cursor.committed_end_utc""",
                (row["stream_id"], row["end_utc"], window_id),
            )

    def summary(self) -> dict[str, int]:
        with self.session() as connection:
            result = {}
            for table in (
                "acquisition",
                "launch_current",
                "record_sighting",
                "observation_event",
                "state_conflict",
                "raw_request",
            ):
                result[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return result

    def revision(self) -> int:
        with self.session() as connection:
            row = connection.execute("SELECT value FROM state_meta WHERE key='revision'").fetchone()
            return int(row[0])

    def current_records(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT raw_record_json, last_seen_at_utc, raw_artifact "
                "FROM launch_current ORDER BY launch_id"
            ).fetchall()
            return [
                (
                    json.loads(row["raw_record_json"]),
                    {
                        "observed_at_utc": row["last_seen_at_utc"],
                        "artifact": row["raw_artifact"],
                    },
                )
                for row in rows
            ]

    def observation_records(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """SELECT ROW_NUMBER() OVER (
                     PARTITION BY event.launch_id ORDER BY event.source_updated_at_utc,
                     event.observed_at_utc, event.acquisition_id, event.record_ordinal
                   ) AS observation_sequence, event.acquisition_id, event.record_ordinal,
                   event.launch_id, event.record_hash,
                   event.source_updated_at_utc, event.observed_at_utc,
                   sighting.raw_artifact, sighting.raw_record_json
                   FROM observation_event AS event
                   JOIN record_sighting AS sighting
                     ON event.acquisition_id=sighting.acquisition_id
                    AND event.record_ordinal=sighting.record_ordinal
                   ORDER BY event.observation_id"""
            ).fetchall()
            result = []
            for row in rows:
                record = json.loads(row["raw_record_json"])
                precision = record.get("net_precision") or {}
                net = timestamp(record.get("net"))
                interval = schedule_interval(net, precision)
                result.append(
                    {
                        "observation_sequence": row["observation_sequence"],
                        "observation_event_key": observation_event_key(
                            row["acquisition_id"], row["record_ordinal"]
                        ),
                        "acquisition_id": row["acquisition_id"],
                        "record_ordinal": row["record_ordinal"],
                        "launch_id": row["launch_id"],
                        "record_hash": row["record_hash"],
                        "source_updated_at_utc": datetime.fromisoformat(
                            row["source_updated_at_utc"]
                        ),
                        "observed_at_utc": datetime.fromisoformat(row["observed_at_utc"]),
                        "raw_artifact": row["raw_artifact"],
                        "parser_version": PARSER_VERSION,
                        "semantic_hash_version": HASH_VERSION,
                        "net_utc": net,
                        "net_precision_id": precision.get("id"),
                        "net_precision_name": precision.get("name"),
                        "schedule_earliest_utc": interval.earliest_utc,
                        "schedule_latest_exclusive_utc": interval.latest_exclusive_utc,
                        "interval_basis": interval.interval_basis,
                        "precision_supported": interval.precision_supported,
                    }
                )
            return result

    def acquisition_records(self) -> list[dict[str, Any]]:
        """Return accepted acquisition and frozen-window provenance in stable order."""
        with self.session() as connection:
            rows = connection.execute(
                """SELECT acquisition.acquisition_id, acquisition.source_run_id,
                   acquisition.observed_at_utc, acquisition.applied_at_utc,
                   window.window_id, window.plan_id, window.stream_id,
                   window.start_utc, window.end_utc, window.query_json,
                   window.pass_number, window.extraction_status, window.reconciled,
                   plan.plan_type, plan.frozen_as_of_utc
                   FROM acquisition
                   LEFT JOIN extraction_window AS window
                     ON window.window_id=acquisition.source_run_id
                   LEFT JOIN extraction_plan AS plan ON plan.plan_id=window.plan_id
                   ORDER BY acquisition.acquisition_id"""
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item["query_json"] is not None:
                    item["query"] = json.loads(item.pop("query_json"))
                else:
                    item.pop("query_json")
                result.append(item)
            return result
