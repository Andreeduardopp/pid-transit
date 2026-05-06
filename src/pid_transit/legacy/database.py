"""
GTFS SQLite database layer.

Exposes :class:`GtfsDatabase` — a thin wrapper around a SQLite file that
mirrors the GTFS specification with full referential integrity (PKs, FKs,
NOT NULL).  The caller owns the path; this module has no knowledge of
data-lake conventions or project layouts.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

from .schemas import GTFS_TABLE_MODELS

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0.0"


# ═══════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class InsertResult:
    """Result of an upsert operation."""
    inserted: int = 0
    updated: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class IntegrityViolation:
    """A single referential-integrity or consistency violation."""
    table: str = ""
    record_id: str = ""
    violation_type: str = ""   # "orphaned_fk", "missing_required"
    detail: str = ""


@dataclass
class IntegrityReport:
    """Result of an integrity check."""
    is_clean: bool = True
    violations: List[IntegrityViolation] = field(default_factory=list)
    table_counts: Dict[str, int] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Table definitions — column name, SQL type, constraints
# ═══════════════════════════════════════════════════════════════════════════

# The order in each list is the canonical GTFS column order used during export.

_TABLE_SCHEMAS: Dict[str, str] = {
    "agency": """
        CREATE TABLE IF NOT EXISTS agency (
            agency_id        TEXT PRIMARY KEY,
            agency_name      TEXT NOT NULL,
            agency_url       TEXT NOT NULL,
            agency_timezone  TEXT NOT NULL,
            agency_lang      TEXT,
            agency_phone     TEXT,
            agency_fare_url  TEXT,
            agency_email     TEXT
        )
    """,
    "stops": """
        CREATE TABLE IF NOT EXISTS stops (
            stop_id              TEXT PRIMARY KEY,
            stop_name            TEXT,
            stop_lat             REAL,
            stop_lon             REAL,
            stop_code            TEXT,
            stop_desc            TEXT,
            zone_id              TEXT,
            stop_url             TEXT,
            location_type        INTEGER,
            parent_station       TEXT,
            stop_timezone        TEXT,
            wheelchair_boarding  INTEGER,
            level_id             TEXT,
            platform_code        TEXT
        )
    """,
    "routes": """
        CREATE TABLE IF NOT EXISTS routes (
            route_id             TEXT PRIMARY KEY,
            agency_id            TEXT REFERENCES agency(agency_id),
            route_short_name     TEXT,
            route_long_name      TEXT,
            route_desc           TEXT,
            route_type           INTEGER NOT NULL,
            route_url            TEXT,
            route_color          TEXT,
            route_text_color     TEXT,
            route_sort_order     INTEGER,
            continuous_pickup    INTEGER,
            continuous_drop_off  INTEGER
        )
    """,
    "trips": """
        CREATE TABLE IF NOT EXISTS trips (
            route_id                TEXT NOT NULL REFERENCES routes(route_id),
            service_id              TEXT NOT NULL,
            trip_id                 TEXT PRIMARY KEY,
            trip_headsign           TEXT,
            trip_short_name         TEXT,
            direction_id            INTEGER,
            block_id                TEXT,
            shape_id                TEXT,
            wheelchair_accessible   INTEGER,
            bikes_allowed           INTEGER
        )
    """,
    "stop_times": """
        CREATE TABLE IF NOT EXISTS stop_times (
            trip_id              TEXT NOT NULL REFERENCES trips(trip_id),
            arrival_time         TEXT,
            departure_time       TEXT,
            stop_id              TEXT NOT NULL REFERENCES stops(stop_id),
            stop_sequence        INTEGER NOT NULL,
            stop_headsign        TEXT,
            pickup_type          INTEGER,
            drop_off_type        INTEGER,
            continuous_pickup    INTEGER,
            continuous_drop_off  INTEGER,
            shape_dist_traveled  REAL,
            timepoint            INTEGER,
            PRIMARY KEY (trip_id, stop_sequence)
        )
    """,
    "calendar": """
        CREATE TABLE IF NOT EXISTS calendar (
            service_id   TEXT PRIMARY KEY,
            monday       INTEGER NOT NULL,
            tuesday      INTEGER NOT NULL,
            wednesday    INTEGER NOT NULL,
            thursday     INTEGER NOT NULL,
            friday       INTEGER NOT NULL,
            saturday     INTEGER NOT NULL,
            sunday       INTEGER NOT NULL,
            start_date   TEXT NOT NULL,
            end_date     TEXT NOT NULL
        )
    """,
    "calendar_dates": """
        CREATE TABLE IF NOT EXISTS calendar_dates (
            service_id       TEXT NOT NULL,
            date             TEXT NOT NULL,
            exception_type   INTEGER NOT NULL,
            PRIMARY KEY (service_id, date)
        )
    """,
    "shapes": """
        CREATE TABLE IF NOT EXISTS shapes (
            shape_id             TEXT NOT NULL,
            shape_pt_lat         REAL NOT NULL,
            shape_pt_lon         REAL NOT NULL,
            shape_pt_sequence    INTEGER NOT NULL,
            shape_dist_traveled  REAL,
            PRIMARY KEY (shape_id, shape_pt_sequence)
        )
    """,
    "frequencies": """
        CREATE TABLE IF NOT EXISTS frequencies (
            trip_id       TEXT NOT NULL REFERENCES trips(trip_id),
            start_time    TEXT NOT NULL,
            end_time      TEXT NOT NULL,
            headway_secs  INTEGER NOT NULL,
            exact_times   INTEGER,
            PRIMARY KEY (trip_id, start_time)
        )
    """,
    "transfers": """
        CREATE TABLE IF NOT EXISTS transfers (
            from_stop_id       TEXT NOT NULL REFERENCES stops(stop_id),
            to_stop_id         TEXT NOT NULL REFERENCES stops(stop_id),
            transfer_type      INTEGER NOT NULL,
            min_transfer_time  INTEGER,
            PRIMARY KEY (from_stop_id, to_stop_id)
        )
    """,
    "feed_info": """
        CREATE TABLE IF NOT EXISTS feed_info (
            feed_publisher_name  TEXT NOT NULL,
            feed_publisher_url   TEXT NOT NULL,
            feed_lang            TEXT NOT NULL,
            default_lang         TEXT,
            feed_start_date      TEXT,
            feed_end_date        TEXT,
            feed_version         TEXT,
            feed_contact_email   TEXT,
            feed_contact_url     TEXT
        )
    """,
    # ─── GTFS-ride extension ──────────────────────────────────────────────
    "board_alight": """
        CREATE TABLE IF NOT EXISTS board_alight (
            trip_id                  TEXT NOT NULL REFERENCES trips(trip_id),
            stop_id                  TEXT NOT NULL REFERENCES stops(stop_id),
            stop_sequence            INTEGER NOT NULL,
            record_use               INTEGER NOT NULL,
            schedule_relationship    INTEGER,
            boardings                INTEGER,
            alightings               INTEGER,
            current_load             INTEGER,
            load_count_method        INTEGER,
            load_type                INTEGER,
            rack_down                INTEGER,
            bike_boardings           INTEGER,
            bike_alightings          INTEGER,
            ramp_used                INTEGER,
            ramp_boardings           INTEGER,
            ramp_alightings          INTEGER,
            service_date             TEXT,
            service_arrival_time     TEXT,
            service_departure_time   TEXT,
            source                   TEXT,
            PRIMARY KEY (trip_id, stop_id, stop_sequence)
        )
    """,
    "ridership": """
        CREATE TABLE IF NOT EXISTS ridership (
            total_boardings       INTEGER NOT NULL,
            total_alightings      INTEGER NOT NULL,
            ridership_start_date  TEXT NOT NULL,
            ridership_end_date    TEXT NOT NULL,
            service_id            TEXT,
            monday_through_sunday TEXT,
            trip_id               TEXT,
            route_id              TEXT,
            direction_id          INTEGER,
            stop_id               TEXT
        )
    """,
    "ride_feed_info": """
        CREATE TABLE IF NOT EXISTS ride_feed_info (
            ride_files              TEXT NOT NULL,
            ride_start_date         TEXT NOT NULL,
            ride_end_date           TEXT NOT NULL,
            gtfs_feed_date          TEXT,
            default_currency_type   TEXT,
            ride_feed_version       TEXT
        )
    """,
    "trip_capacity": """
        CREATE TABLE IF NOT EXISTS trip_capacity (
            trip_id               TEXT NOT NULL REFERENCES trips(trip_id),
            service_date          TEXT,
            vehicle_description   TEXT,
            seated_capacity       INTEGER,
            standing_capacity     INTEGER,
            wheelchair_capacity   INTEGER,
            bike_capacity         INTEGER,
            PRIMARY KEY (trip_id, service_date)
        )
    """,
    # ─── Internal meta table ──────────────────────────────────────────────
    "_gtfs_meta": """
        CREATE TABLE IF NOT EXISTS _gtfs_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """,
}

# Primary keys for each table (used for upsert and delete)
_TABLE_PK: Dict[str, List[str]] = {
    "agency":         ["agency_id"],
    "stops":          ["stop_id"],
    "routes":         ["route_id"],
    "trips":          ["trip_id"],
    "stop_times":     ["trip_id", "stop_sequence"],
    "calendar":       ["service_id"],
    "calendar_dates": ["service_id", "date"],
    "shapes":         ["shape_id", "shape_pt_sequence"],
    "frequencies":    ["trip_id", "start_time"],
    "transfers":      ["from_stop_id", "to_stop_id"],
    "feed_info":      [],
    "board_alight":   ["trip_id", "stop_id", "stop_sequence"],
    "ridership":      [],
    "ride_feed_info": [],
    "trip_capacity":  ["trip_id", "service_date"],
}

# FK relationships checked during integrity analysis
_FK_CHECKS: List[Dict[str, str]] = [
    {"table": "routes",        "column": "agency_id",    "ref_table": "agency",  "ref_col": "agency_id"},
    {"table": "trips",         "column": "route_id",     "ref_table": "routes",  "ref_col": "route_id"},
    {"table": "stop_times",    "column": "trip_id",      "ref_table": "trips",   "ref_col": "trip_id"},
    {"table": "stop_times",    "column": "stop_id",      "ref_table": "stops",   "ref_col": "stop_id"},
    {"table": "frequencies",   "column": "trip_id",      "ref_table": "trips",   "ref_col": "trip_id"},
    {"table": "transfers",     "column": "from_stop_id", "ref_table": "stops",   "ref_col": "stop_id"},
    {"table": "transfers",     "column": "to_stop_id",   "ref_table": "stops",   "ref_col": "stop_id"},
    {"table": "board_alight",  "column": "trip_id",      "ref_table": "trips",   "ref_col": "trip_id"},
    {"table": "board_alight",  "column": "stop_id",      "ref_table": "stops",   "ref_col": "stop_id"},
    {"table": "trip_capacity", "column": "trip_id",      "ref_table": "trips",   "ref_col": "trip_id"},
]

# Columns in canonical GTFS order for each table (populated at import time).
_TABLE_COLUMNS: Dict[str, List[str]] = {}


def _build_column_index() -> None:
    """Parse CREATE TABLE statements to extract ordered column names."""
    for tbl, ddl in _TABLE_SCHEMAS.items():
        if tbl.startswith("_"):
            continue
        cols: List[str] = []
        for line in ddl.splitlines():
            line = line.strip().rstrip(",")
            m = re.match(r"^(\w+)\s+(TEXT|REAL|INTEGER)", line)
            if m:
                cols.append(m.group(1))
        _TABLE_COLUMNS[tbl] = cols


_build_column_index()


# FK-safe delete order — children first, parents last.
_CLEAR_ORDER: List[str] = [
    "board_alight",
    "ridership",
    "trip_capacity",
    "stop_times",
    "frequencies",
    "transfers",
    "trips",
    "routes",
    "shapes",
    "calendar_dates",
    "calendar",
    "stops",
    "agency",
    "feed_info",
    "ride_feed_info",
]


# ═══════════════════════════════════════════════════════════════════════════
# Connection-level helpers (used by importer to batch upserts in one tx)
# ═══════════════════════════════════════════════════════════════════════════

def upsert_records_on_conn(
    conn: sqlite3.Connection,
    table_name: str,
    records: List[Dict[str, Any]],
) -> InsertResult:
    """Validate + upsert records on an open connection.

    Does not commit, close, or touch ``_gtfs_meta``.  Use this to batch
    multiple upserts inside a single transaction.  For a one-shot upsert
    with its own transaction, use :meth:`GtfsDatabase.upsert`.
    """
    if table_name not in GTFS_TABLE_MODELS:
        return InsertResult(
            failed=len(records),
            errors=[f"Unknown GTFS table: {table_name}"],
        )

    model_cls = GTFS_TABLE_MODELS[table_name]
    columns = _TABLE_COLUMNS.get(table_name, [])
    if not columns:
        return InsertResult(
            failed=len(records),
            errors=[f"No column schema for table: {table_name}"],
        )

    result = InsertResult()
    valid_rows: List[Dict[str, Any]] = []

    for i, raw in enumerate(records):
        try:
            obj = model_cls(**raw)
            row_dict = obj.model_dump()
            valid_rows.append({c: row_dict.get(c) for c in columns})
        except (ValidationError, Exception) as exc:
            result.failed += 1
            result.errors.append(f"Record {i}: {exc}")

    if not valid_rows:
        return result

    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(columns)
    sql = f"INSERT OR REPLACE INTO {table_name} ({col_list}) VALUES ({placeholders})"

    for row in valid_rows:
        try:
            conn.execute(sql, [row.get(c) for c in columns])
            result.inserted += 1
        except sqlite3.IntegrityError as exc:
            result.failed += 1
            result.errors.append(str(exc))

    return result


def get_table_columns(table_name: str) -> List[str]:
    """Return the ordered list of columns for a GTFS table."""
    return list(_TABLE_COLUMNS.get(table_name, []))


# ═══════════════════════════════════════════════════════════════════════════
# GtfsDatabase — the public class
# ═══════════════════════════════════════════════════════════════════════════

class GtfsDatabase:
    """A GTFS-compliant SQLite database at a user-chosen path.

    Construction is idempotent: if the file and schema already exist, the
    constructor leaves existing data alone.  The parent directory is
    created on demand.
    """

    def __init__(self, db_path: Union[str, Path]):
        self.db_path: Path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # ─── Constructors ────────────────────────────────────────────────────

    @classmethod
    def from_slug(
        cls,
        slug: str,
        root: Optional[Union[str, Path]] = None,
    ) -> "GtfsDatabase":
        """Build a ``GtfsDatabase`` at ``<root>/<slug>.db``.

        ``root`` defaults to the current working directory.
        """
        base = Path(root) if root is not None else Path.cwd()
        return cls(base / f"{slug}.db")

    # ─── Lifecycle ───────────────────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open a connection with FK enforcement and row factory enabled."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def exists(self) -> bool:
        """Return True if the underlying database file exists on disk."""
        return self.db_path.exists()

    def _ensure_schema(self) -> None:
        """Create all tables if missing.  Seeds meta timestamps."""
        conn = self.connect()
        try:
            for ddl in _TABLE_SCHEMAS.values():
                conn.execute(ddl)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO _gtfs_meta (key, value) VALUES (?, ?)",
                ("schema_version", SCHEMA_VERSION),
            )
            conn.execute(
                "INSERT OR IGNORE INTO _gtfs_meta (key, value) VALUES (?, ?)",
                ("created_at", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO _gtfs_meta (key, value) VALUES (?, ?)",
                ("last_modified", now),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("GTFS database ready at %s", self.db_path)

    def _touch(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO _gtfs_meta (key, value) VALUES (?, ?)",
            ("last_modified", datetime.now(timezone.utc).isoformat()),
        )

    # ─── CRUD ────────────────────────────────────────────────────────────

    def upsert(self, table_name: str, records: List[Dict[str, Any]]) -> InsertResult:
        """Validate and insert/update records into a GTFS table.

        Records are validated against the matching Pydantic model before
        being written.  Failed rows are collected in the returned
        :class:`InsertResult`; valid rows are committed in a single
        transaction.
        """
        conn = self.connect()
        try:
            result = upsert_records_on_conn(conn, table_name, records)
            self._touch(conn)
            conn.commit()
        finally:
            conn.close()
        return result

    def get_records(
        self,
        table_name: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Read rows from a GTFS table, paginated."""
        conn = self.connect()
        try:
            cur = conn.execute(
                f"SELECT * FROM {table_name} LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def count(self, table_name: str) -> int:
        """Count rows in a GTFS table."""
        conn = self.connect()
        try:
            cur = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
            return cur.fetchone()[0]
        finally:
            conn.close()

    def delete(self, table_name: str, ids: List[str]) -> int:
        """Delete records by primary key.

        For single-PK tables, ``ids`` contains PK values directly.  For
        composite-PK tables, each element is a ``|``-separated string of
        the PK component values (e.g. ``"trip_1|3"`` for ``stop_times``).
        """
        pk_cols = _TABLE_PK.get(table_name, [])
        if not pk_cols:
            return 0
        conn = self.connect()
        deleted = 0
        try:
            for pk_val in ids:
                parts = pk_val.split("|") if len(pk_cols) > 1 else [pk_val]
                where = " AND ".join(f"{c} = ?" for c in pk_cols)
                cur = conn.execute(
                    f"DELETE FROM {table_name} WHERE {where}", parts
                )
                deleted += cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return deleted

    def clear(self, table_name: str) -> int:
        """Delete every row from a single GTFS table."""
        conn = self.connect()
        try:
            cur = conn.execute(f"DELETE FROM {table_name}")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def clear_all(self) -> Dict[str, int]:
        """Delete every row from every GTFS table in FK-safe order.

        Returns a mapping of table name → rows deleted.  Tables that do
        not exist yet are silently skipped.
        """
        deleted: Dict[str, int] = {}
        conn = self.connect()
        try:
            for tbl in _CLEAR_ORDER:
                try:
                    cur = conn.execute(f"DELETE FROM {tbl}")
                    deleted[tbl] = cur.rowcount
                except sqlite3.OperationalError:
                    continue
            self._touch(conn)
            conn.commit()
        finally:
            conn.close()
        return deleted

    # ─── Integrity + summary ─────────────────────────────────────────────

    def check_integrity(self) -> IntegrityReport:
        """Run FK and consistency checks against the database."""
        report = IntegrityReport()
        conn = self.connect()
        try:
            for tbl in _TABLE_COLUMNS:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
                    report.table_counts[tbl] = cur.fetchone()[0]
                except sqlite3.OperationalError:
                    report.table_counts[tbl] = 0

            for fk in _FK_CHECKS:
                tbl = fk["table"]
                col = fk["column"]
                ref_tbl = fk["ref_table"]
                ref_col = fk["ref_col"]

                if report.table_counts.get(tbl, 0) == 0:
                    continue
                if report.table_counts.get(ref_tbl, 0) == 0 and report.table_counts.get(tbl, 0) > 0:
                    cur = conn.execute(
                        f"SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL"
                    )
                    for row in cur.fetchall():
                        report.violations.append(IntegrityViolation(
                            table=tbl,
                            record_id=str(row[0]),
                            violation_type="orphaned_fk",
                            detail=f"{tbl}.{col}='{row[0]}' references {ref_tbl}.{ref_col} but {ref_tbl} is empty",
                        ))
                    continue

                sql = (
                    f"SELECT DISTINCT t.{col} FROM {tbl} t "
                    f"LEFT JOIN {ref_tbl} r ON t.{col} = r.{ref_col} "
                    f"WHERE r.{ref_col} IS NULL AND t.{col} IS NOT NULL"
                )
                try:
                    cur = conn.execute(sql)
                    for row in cur.fetchall():
                        report.violations.append(IntegrityViolation(
                            table=tbl,
                            record_id=str(row[0]),
                            violation_type="orphaned_fk",
                            detail=f"{tbl}.{col}='{row[0]}' has no matching {ref_tbl}.{ref_col}",
                        ))
                except sqlite3.OperationalError:
                    pass
        finally:
            conn.close()

        report.is_clean = len(report.violations) == 0
        return report

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict for the database.

        Includes row counts per table, file size, timestamps, and
        integrity status.
        """
        if not self.db_path.exists():
            return {"exists": False}

        conn = self.connect()
        try:
            counts: Dict[str, int] = {}
            for tbl in _TABLE_COLUMNS:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
                    counts[tbl] = cur.fetchone()[0]
                except sqlite3.OperationalError:
                    counts[tbl] = 0

            meta: Dict[str, str] = {}
            try:
                for row in conn.execute("SELECT key, value FROM _gtfs_meta"):
                    meta[row["key"]] = row["value"]
            except sqlite3.OperationalError:
                pass
        finally:
            conn.close()

        report = self.check_integrity()
        return {
            "exists": True,
            "path": str(self.db_path),
            "size_bytes": self.db_path.stat().st_size,
            "table_counts": counts,
            "total_records": sum(counts.values()),
            "populated_tables": [t for t, c in counts.items() if c > 0],
            "empty_tables": [t for t, c in counts.items() if c == 0],
            "schema_version": meta.get("schema_version", "unknown"),
            "created_at": meta.get("created_at", "unknown"),
            "last_modified": meta.get("last_modified", "unknown"),
            "integrity_clean": report.is_clean,
            "violation_count": len(report.violations),
        }
