"""
Transmodel SQLite Database.

This module provides a SQLite wrapper designed to hold Transmodel entities
with full referential integrity.
"""

import sqlite3
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional
from pathlib import Path
from datetime import datetime, timezone

from .schemas import TRANSMODEL_ENTITIES

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "3.2.0-transmodel"

_TABLE_SCHEMAS = {
    "operator": """
        CREATE TABLE IF NOT EXISTS operator (
            id       TEXT PRIMARY KEY,
            name     TEXT NOT NULL,
            url      TEXT,
            timezone TEXT NOT NULL,
            lang     TEXT,
            phone    TEXT,
            extras   TEXT
        )
    """,
    "line": """
        CREATE TABLE IF NOT EXISTS line (
            id             TEXT PRIMARY KEY,
            operator_id    TEXT REFERENCES operator(id),
            name           TEXT NOT NULL,
            short_name     TEXT,
            transport_mode TEXT NOT NULL,
            color          TEXT,
            text_color     TEXT,
            description    TEXT,
            url            TEXT,
            sort_order     INTEGER,
            extras         TEXT
        )
    """,
    "scheduled_stop_point": """
        CREATE TABLE IF NOT EXISTS scheduled_stop_point (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            lat                 REAL,
            lon                 REAL,
            stop_area_id        TEXT,
            wheelchair_boarding INTEGER,
            code                TEXT,
            platform_code       TEXT,
            zone_id             TEXT,
            description         TEXT,
            extras              TEXT
        )
    """,
    "level": """
        CREATE TABLE IF NOT EXISTS level (
            id    TEXT PRIMARY KEY,
            "index" REAL NOT NULL,
            name  TEXT,
            extras TEXT
        )
    """,
    "stop_area": """
        CREATE TABLE IF NOT EXISTS stop_area (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            lat                 REAL,
            lon                 REAL,
            location_type       INTEGER NOT NULL DEFAULT 1,
            parent_id           TEXT,
            level_id            TEXT REFERENCES level(id),
            wheelchair_boarding INTEGER,
            extras              TEXT
        )
    """,
    "pathway": """
        CREATE TABLE IF NOT EXISTS pathway (
            id                     TEXT PRIMARY KEY,
            from_stop_id           TEXT NOT NULL,
            to_stop_id             TEXT NOT NULL,
            pathway_mode           INTEGER NOT NULL,
            is_bidirectional       INTEGER NOT NULL,
            length                 REAL,
            traversal_time         INTEGER,
            stair_count            INTEGER,
            max_slope              REAL,
            min_width              REAL,
            signposted_as          TEXT,
            reversed_signposted_as TEXT,
            extras                 TEXT
        )
    """,
    "day_type": """
        CREATE TABLE IF NOT EXISTS day_type (
            id         TEXT PRIMARY KEY,
            monday     INTEGER NOT NULL,
            tuesday    INTEGER NOT NULL,
            wednesday  INTEGER NOT NULL,
            thursday   INTEGER NOT NULL,
            friday     INTEGER NOT NULL,
            saturday   INTEGER NOT NULL,
            sunday     INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date   TEXT NOT NULL,
            extras     TEXT
        )
    """,
    "operating_day_exception": """
        CREATE TABLE IF NOT EXISTS operating_day_exception (
            day_type_id TEXT NOT NULL REFERENCES day_type(id),
            date        TEXT NOT NULL,
            is_addition INTEGER NOT NULL,
            extras      TEXT,
            PRIMARY KEY (day_type_id, date)
        )
    """,
    "journey_pattern": """
        CREATE TABLE IF NOT EXISTS journey_pattern (
            id        TEXT PRIMARY KEY,
            line_id   TEXT NOT NULL REFERENCES line(id),
            direction TEXT,
            extras    TEXT
        )
    """,
    "point_in_journey_pattern": """
        CREATE TABLE IF NOT EXISTS point_in_journey_pattern (
            journey_pattern_id TEXT NOT NULL REFERENCES journey_pattern(id),
            stop_point_id      TEXT NOT NULL REFERENCES scheduled_stop_point(id),
            "order"            INTEGER NOT NULL,
            extras             TEXT,
            PRIMARY KEY (journey_pattern_id, "order")
        )
    """,
    "service_journey": """
        CREATE TABLE IF NOT EXISTS service_journey (
            id                    TEXT PRIMARY KEY,
            line_id               TEXT NOT NULL REFERENCES line(id),
            journey_pattern_id    TEXT REFERENCES journey_pattern(id),
            day_type_id           TEXT NOT NULL REFERENCES day_type(id),
            departure_time        TEXT NOT NULL,
            wheelchair_accessible INTEGER,
            bikes_allowed         INTEGER,
            shape_id              TEXT,
            headsign              TEXT,
            short_name            TEXT,
            block_id              TEXT,
            extras                TEXT
        )
    """,
    "passing_time": """
        CREATE TABLE IF NOT EXISTS passing_time (
            service_journey_id  TEXT NOT NULL REFERENCES service_journey(id),
            stop_point_id       TEXT NOT NULL REFERENCES scheduled_stop_point(id),
            "order"             INTEGER NOT NULL,
            arrival_time        TEXT,
            departure_time      TEXT,
            pickup_type         INTEGER,
            drop_off_type       INTEGER,
            timepoint           INTEGER,
            stop_headsign       TEXT,
            shape_dist_traveled REAL,
            extras              TEXT,
            PRIMARY KEY (service_journey_id, "order")
        )
    """,
    "feed_info": """
        CREATE TABLE IF NOT EXISTS feed_info (
            id              TEXT PRIMARY KEY,
            publisher_name  TEXT NOT NULL,
            publisher_url   TEXT NOT NULL,
            lang            TEXT NOT NULL,
            start_date      TEXT,
            end_date        TEXT,
            version         TEXT,
            contact_email   TEXT,
            contact_url     TEXT,
            extras          TEXT
        )
    """,
    "shape_point": """
        CREATE TABLE IF NOT EXISTS shape_point (
            shape_id      TEXT NOT NULL,
            lat           REAL NOT NULL,
            lon           REAL NOT NULL,
            sequence      INTEGER NOT NULL,
            dist_traveled REAL,
            extras        TEXT,
            PRIMARY KEY (shape_id, sequence)
        )
    """,
    "frequency": """
        CREATE TABLE IF NOT EXISTS frequency (
            service_journey_id TEXT NOT NULL REFERENCES service_journey(id),
            start_time         TEXT NOT NULL,
            end_time           TEXT NOT NULL,
            headway_secs       INTEGER NOT NULL,
            exact_times        INTEGER DEFAULT 0,
            extras             TEXT,
            PRIMARY KEY (service_journey_id, start_time)
        )
    """,
    "transfer": """
        CREATE TABLE IF NOT EXISTS transfer (
            from_stop_id      TEXT NOT NULL REFERENCES scheduled_stop_point(id),
            to_stop_id        TEXT NOT NULL REFERENCES scheduled_stop_point(id),
            transfer_type     INTEGER NOT NULL DEFAULT 0,
            min_transfer_time INTEGER,
            extras            TEXT,
            PRIMARY KEY (from_stop_id, to_stop_id)
        )
    """,
    "fare_attribute": """
        CREATE TABLE IF NOT EXISTS fare_attribute (
            id                TEXT PRIMARY KEY,
            price             REAL NOT NULL,
            currency_type     TEXT NOT NULL,
            payment_method    INTEGER NOT NULL,
            transfers         INTEGER,
            operator_id       TEXT REFERENCES operator(id),
            transfer_duration INTEGER,
            extras            TEXT
        )
    """,
    "fare_rule": """
        CREATE TABLE IF NOT EXISTS fare_rule (
            fare_id        TEXT NOT NULL REFERENCES fare_attribute(id),
            route_id       TEXT NOT NULL DEFAULT '',
            origin_id      TEXT NOT NULL DEFAULT '',
            destination_id TEXT NOT NULL DEFAULT '',
            contains_id    TEXT NOT NULL DEFAULT '',
            extras         TEXT,
            PRIMARY KEY (fare_id, route_id, origin_id, destination_id, contains_id)
        )
    """,
    "translation": """
        CREATE TABLE IF NOT EXISTS translation (
            table_name   TEXT NOT NULL,
            field_name   TEXT NOT NULL,
            language     TEXT NOT NULL,
            translation  TEXT NOT NULL,
            record_id    TEXT NOT NULL DEFAULT '',
            record_sub_id TEXT NOT NULL DEFAULT '',
            field_value  TEXT NOT NULL DEFAULT '',
            extras       TEXT,
            PRIMARY KEY (table_name, field_name, language, record_id, field_value)
        )
    """,
    "attribution": """
        CREATE TABLE IF NOT EXISTS attribution (
            id                 TEXT PRIMARY KEY,
            organization_name  TEXT NOT NULL,
            is_producer        INTEGER,
            is_operator        INTEGER,
            is_authority       INTEGER,
            attribution_url    TEXT,
            attribution_email  TEXT,
            attribution_phone  TEXT,
            extras             TEXT
        )
    """,
    "_meta": """
        CREATE TABLE IF NOT EXISTS _meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """
}

# Secondary (foreign-key) indexes. Composite primary keys already index their
# leftmost column (passing_time.service_journey_id, point_in_journey_pattern
# .journey_pattern_id, etc.), so only lookups NOT covered by a PK prefix are
# listed here. Dropped and rebuilt around bulk imports (load-then-index).
_INDEX_SCHEMAS = {
    "idx_sj_line":      "CREATE INDEX IF NOT EXISTS idx_sj_line      ON service_journey(line_id)",
    "idx_sj_day_type":  "CREATE INDEX IF NOT EXISTS idx_sj_day_type  ON service_journey(day_type_id)",
    "idx_sj_pattern":   "CREATE INDEX IF NOT EXISTS idx_sj_pattern   ON service_journey(journey_pattern_id)",
    "idx_jp_line":      "CREATE INDEX IF NOT EXISTS idx_jp_line      ON journey_pattern(line_id)",
    "idx_line_op":      "CREATE INDEX IF NOT EXISTS idx_line_op      ON line(operator_id)",
    "idx_pt_stop":      "CREATE INDEX IF NOT EXISTS idx_pt_stop      ON passing_time(stop_point_id)",
    "idx_pijp_stop":    "CREATE INDEX IF NOT EXISTS idx_pijp_stop    ON point_in_journey_pattern(stop_point_id)",
    "idx_ssp_area":     "CREATE INDEX IF NOT EXISTS idx_ssp_area     ON scheduled_stop_point(stop_area_id)",
    "idx_sa_parent":    "CREATE INDEX IF NOT EXISTS idx_sa_parent    ON stop_area(parent_id)",
    "idx_transfer_to":  "CREATE INDEX IF NOT EXISTS idx_transfer_to  ON transfer(to_stop_id)",
    "idx_pathway_from": "CREATE INDEX IF NOT EXISTS idx_pathway_from ON pathway(from_stop_id)",
    "idx_pathway_to":   "CREATE INDEX IF NOT EXISTS idx_pathway_to   ON pathway(to_stop_id)",
}

# Columns added by the 3.2 migration (Task 1 field gaps + extras everywhere).
# Applied idempotently via ALTER TABLE ADD COLUMN when a column is missing.
_MIGRATION_COLUMNS = {
    "line": {"text_color": "TEXT", "description": "TEXT", "url": "TEXT",
             "sort_order": "INTEGER"},
    "scheduled_stop_point": {"code": "TEXT", "platform_code": "TEXT",
                             "zone_id": "TEXT", "description": "TEXT"},
    "service_journey": {"headsign": "TEXT", "short_name": "TEXT",
                        "block_id": "TEXT"},
    "passing_time": {"pickup_type": "INTEGER", "drop_off_type": "INTEGER",
                     "timepoint": "INTEGER", "stop_headsign": "TEXT",
                     "shape_dist_traveled": "REAL"},
}

class TransmodelDatabase:
    """Core Transmodel database wrapper."""

    def __init__(self, db_path: Path | str):
        self._in_memory = str(db_path) == ":memory:"
        self._session_conn: Optional[sqlite3.Connection] = None
        if self._in_memory:
            # Keep one persistent connection so the in-memory database survives
            # across multiple method calls (each new connection to ":memory:"
            # gets its own isolated empty database).
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.row_factory = sqlite3.Row
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = None
        self._ensure_schema()
        self._migrate_schema()

    def connect(self) -> sqlite3.Connection:
        if self._in_memory:
            return self._conn
        if self._session_conn is not None:
            return self._session_conn
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def close(self) -> None:
        """Release any held connection.

        File-backed databases open and close a connection per call, so there is
        usually nothing pinned; this closes the persistent in-memory connection
        and any active bulk-session connection so the OS file handle is released
        deterministically (matters on Windows, where an open handle blocks
        deleting the .db file)."""
        if self._session_conn is not None:
            try:
                self._session_conn.close()
            finally:
                self._session_conn = None
        if self._in_memory and self._conn is not None:
            self._conn.close()
            self._conn = None

    def _release(self, conn: sqlite3.Connection) -> None:
        """Close a connection obtained from connect(), unless it is shared
        (the in-memory connection or an active bulk session)."""
        if not self._in_memory and conn is not self._session_conn:
            conn.close()

    @contextmanager
    def bulk_session(self) -> Iterator[sqlite3.Connection]:
        """Pin one connection for the duration of a bulk import/export run.

        While active, connect() hands out this connection instead of opening
        a fresh one per call. File-backed databases get bulk-load pragmas
        (WAL journal, relaxed fsync) and the _meta.last_modified stamp is
        written once at session end instead of per upsert. Nested sessions
        reuse the outer connection. On exception the transaction is rolled
        back before the connection is released.
        """
        if self._session_conn is not None or self._in_memory:
            # Nested session, or in-memory (already a single shared conn).
            yield self.connect()
            return

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = sqlite3.Row
        self._session_conn = conn
        try:
            # Load-then-index: maintaining secondary indexes row-by-row is
            # slower than rebuilding them over the loaded tables afterwards.
            for name in _INDEX_SCHEMAS:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
            yield conn
            self._touch_last_modified(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            try:
                # Rebuild even after a rollback; a missing index is also
                # self-healed by _ensure_schema on the next open.
                for ddl in _INDEX_SCHEMAS.values():
                    conn.execute(ddl)
                conn.commit()
            except sqlite3.Error:
                logger.warning("Could not rebuild indexes after bulk session")
            self._session_conn = None
            conn.close()

    def _touch_last_modified(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
            ("last_modified", datetime.now(timezone.utc).isoformat()),
        )

    def _ensure_schema(self) -> None:
        conn = self.connect()
        close_after = not self._in_memory
        try:
            for ddl in _TABLE_SCHEMAS.values():
                conn.execute(ddl)
            for ddl in _INDEX_SCHEMAS.values():
                conn.execute(ddl)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO _meta (key, value) VALUES (?, ?)",
                ("schema_version", SCHEMA_VERSION),
            )
            conn.execute(
                "INSERT OR IGNORE INTO _meta (key, value) VALUES (?, ?)",
                ("created_at", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
                ("last_modified", now),
            )
            conn.commit()
        finally:
            if close_after:
                conn.close()

    def _migrate_schema(self) -> None:
        """Migrate an existing database to the current schema version if needed."""
        conn = self.connect()
        close_after = not self._in_memory
        try:
            cur = conn.execute("SELECT value FROM _meta WHERE key = 'schema_version'")
            row = cur.fetchone()
            stored_version = row[0] if row else None

            if stored_version != SCHEMA_VERSION:
                logger.info(
                    "Migrating schema from %s to %s",
                    stored_version or "unknown", SCHEMA_VERSION,
                )
                if stored_version == "3.0.0-transmodel":
                    self._migrate_nullable_stop_coords(conn)
                for ddl in _TABLE_SCHEMAS.values():
                    conn.execute(ddl)
                self._migrate_add_missing_columns(conn)
                for ddl in _INDEX_SCHEMAS.values():
                    conn.execute(ddl)
                conn.execute(
                    "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
                    ("schema_version", SCHEMA_VERSION),
                )
                conn.commit()
        finally:
            if close_after:
                conn.close()

    @staticmethod
    def _migrate_add_missing_columns(conn: sqlite3.Connection) -> None:
        """3.x -> 3.2: add the Task-1 field-gap columns and the `extras`
        column in place. Idempotent — only columns that don't exist yet are
        added, so re-running on a current database is a no-op."""
        for table, cols in _MIGRATION_COLUMNS.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for col, col_type in cols.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        for table in _TABLE_SCHEMAS:
            if table == "_meta":
                continue
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "extras" not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN extras TEXT")

    @staticmethod
    def _migrate_nullable_stop_coords(conn: sqlite3.Connection) -> None:
        """3.0 -> 3.1: lat/lon on scheduled_stop_point became nullable.

        SQLite cannot drop NOT NULL in place, so rebuild the table.
        legacy_alter_table keeps the RENAME from rewriting the foreign keys
        in referencing tables (passing_time etc.) to point at the temp name.
        """
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.execute("ALTER TABLE scheduled_stop_point RENAME TO _ssp_migrate_old")
            conn.execute(_TABLE_SCHEMAS["scheduled_stop_point"])
            conn.execute(
                "INSERT INTO scheduled_stop_point "
                "(id, name, lat, lon, stop_area_id, wheelchair_boarding) "
                "SELECT id, name, lat, lon, stop_area_id, wheelchair_boarding "
                "FROM _ssp_migrate_old"
            )
            conn.execute("DROP TABLE _ssp_migrate_old")
            conn.commit()
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

    def _check_table(self, table_name: str) -> None:
        if table_name not in _TABLE_SCHEMAS:
            raise ValueError(f"Unknown table: {table_name}")

    def upsert(self, table_name: str, records: List[Dict[str, Any]], validate: bool = True) -> int:
        """Upsert records into a Transmodel table.

        With validate=False the per-row Pydantic construction is skipped and
        record dicts are bound directly (missing keys fall back to the model
        defaults). Only use it for rows an adapter built itself and knows the
        shape of; malformed values then surface as sqlite errors instead of
        ValidationErrors.
        """
        if table_name not in TRANSMODEL_ENTITIES:
            raise ValueError(f"Unknown table {table_name}")
        if not records:
            return 0

        model_cls = TRANSMODEL_ENTITIES[table_name]
        fields = model_cls.model_fields
        cols = list(fields)
        # Quote every column: "order" and "index" are SQL keywords
        safe_cols = [f'"{c}"' for c in cols]
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT OR REPLACE INTO {table_name} ({','.join(safe_cols)}) VALUES ({placeholders})"

        if validate:
            rows = [
                tuple(row_dict[c] for c in cols)
                for row_dict in (model_cls(**raw).model_dump() for raw in records)
            ]
        else:
            defaults = {
                name: None if f.is_required() else f.get_default(call_default_factory=True)
                for name, f in fields.items()
            }
            rows = [tuple(raw.get(c, defaults[c]) for c in cols) for raw in records]

        conn = self.connect()
        try:
            conn.executemany(sql, rows)
            if self._session_conn is None:
                # Inside a bulk session the stamp is written once at session end.
                self._touch_last_modified(conn)
            conn.commit()
        finally:
            self._release(conn)
        return len(records)

    def get_records(self, table_name: str) -> List[Dict[str, Any]]:
        """Fetch all records from a table."""
        self._check_table(table_name)
        conn = self.connect()
        try:
            cur = conn.execute(f"SELECT * FROM {table_name}")
            return [dict(row) for row in cur.fetchall()]
        finally:
            self._release(conn)

    def query(
        self,
        table_name: str,
        where: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch records with optional filtering, ordering, and limit."""
        self._check_table(table_name)
        sql = f"SELECT * FROM {table_name}"
        params: list = []
        if where:
            clauses = []
            for col, val in where.items():
                clauses.append(f'"{col}" = ?')
                params.append(val)
            sql += " WHERE " + " AND ".join(clauses)
        if order_by:
            sql += f' ORDER BY "{order_by}"'
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
        finally:
            self._release(conn)

    def get_one(
        self, table_name: str, where: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single record matching the where clause, or None."""
        results = self.query(table_name, where=where, limit=1)
        return results[0] if results else None

    def count(
        self, table_name: str, where: Optional[Dict[str, Any]] = None
    ) -> int:
        """Count records, optionally filtered."""
        self._check_table(table_name)
        sql = f"SELECT COUNT(*) FROM {table_name}"
        params: list = []
        if where:
            clauses = []
            for col, val in where.items():
                clauses.append(f'"{col}" = ?')
                params.append(val)
            sql += " WHERE " + " AND ".join(clauses)
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            return cur.fetchone()[0]
        finally:
            self._release(conn)

    def delete(self, table_name: str, where: Dict[str, Any]) -> int:
        """Delete records matching the where clause. Returns count deleted."""
        self._check_table(table_name)
        if not where:
            raise ValueError("where clause required for delete")
        clauses = []
        params: list = []
        for col, val in where.items():
            clauses.append(f'"{col}" = ?')
            params.append(val)
        sql = f"DELETE FROM {table_name} WHERE " + " AND ".join(clauses)
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        finally:
            self._release(conn)
