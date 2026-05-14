"""
Transmodel SQLite Database.

This module provides a SQLite wrapper designed to hold Transmodel entities
with full referential integrity.
"""

import sqlite3
from typing import Any, Dict, List, Optional
from pathlib import Path
from datetime import datetime, timezone

from .schemas import TRANSMODEL_ENTITIES

SCHEMA_VERSION = "2.0.0-transmodel"

_TABLE_SCHEMAS = {
    "operator": """
        CREATE TABLE IF NOT EXISTS operator (
            id       TEXT PRIMARY KEY,
            name     TEXT NOT NULL,
            url      TEXT,
            timezone TEXT NOT NULL,
            lang     TEXT,
            phone    TEXT
        )
    """,
    "line": """
        CREATE TABLE IF NOT EXISTS line (
            id             TEXT PRIMARY KEY,
            operator_id    TEXT REFERENCES operator(id),
            name           TEXT NOT NULL,
            short_name     TEXT,
            transport_mode TEXT NOT NULL,
            color          TEXT
        )
    """,
    "scheduled_stop_point": """
        CREATE TABLE IF NOT EXISTS scheduled_stop_point (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            lat                 REAL NOT NULL,
            lon                 REAL NOT NULL,
            stop_area_id        TEXT,
            wheelchair_boarding INTEGER
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
            end_date   TEXT NOT NULL
        )
    """,
    "operating_day_exception": """
        CREATE TABLE IF NOT EXISTS operating_day_exception (
            day_type_id TEXT NOT NULL REFERENCES day_type(id),
            date        TEXT NOT NULL,
            is_addition INTEGER NOT NULL,
            PRIMARY KEY (day_type_id, date)
        )
    """,
    "journey_pattern": """
        CREATE TABLE IF NOT EXISTS journey_pattern (
            id        TEXT PRIMARY KEY,
            line_id   TEXT NOT NULL REFERENCES line(id),
            direction TEXT
        )
    """,
    "point_in_journey_pattern": """
        CREATE TABLE IF NOT EXISTS point_in_journey_pattern (
            journey_pattern_id TEXT NOT NULL REFERENCES journey_pattern(id),
            stop_point_id      TEXT NOT NULL REFERENCES scheduled_stop_point(id),
            "order"            INTEGER NOT NULL,
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
            shape_id              TEXT
        )
    """,
    "passing_time": """
        CREATE TABLE IF NOT EXISTS passing_time (
            service_journey_id TEXT NOT NULL REFERENCES service_journey(id),
            stop_point_id      TEXT NOT NULL REFERENCES scheduled_stop_point(id),
            "order"            INTEGER NOT NULL,
            arrival_time       TEXT,
            departure_time     TEXT,
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
            contact_url     TEXT
        )
    """,
    "shape_point": """
        CREATE TABLE IF NOT EXISTS shape_point (
            shape_id      TEXT NOT NULL,
            lat           REAL NOT NULL,
            lon           REAL NOT NULL,
            sequence      INTEGER NOT NULL,
            dist_traveled REAL,
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
            PRIMARY KEY (service_journey_id, start_time)
        )
    """,
    "transfer": """
        CREATE TABLE IF NOT EXISTS transfer (
            from_stop_id      TEXT NOT NULL REFERENCES scheduled_stop_point(id),
            to_stop_id        TEXT NOT NULL REFERENCES scheduled_stop_point(id),
            transfer_type     INTEGER NOT NULL DEFAULT 0,
            min_transfer_time INTEGER,
            PRIMARY KEY (from_stop_id, to_stop_id)
        )
    """,
    "_meta": """
        CREATE TABLE IF NOT EXISTS _meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """
}

class TransmodelDatabase:
    """Core Transmodel database wrapper."""

    def __init__(self, db_path: Path | str):
        self._in_memory = str(db_path) == ":memory:"
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

    def connect(self) -> sqlite3.Connection:
        if self._in_memory:
            return self._conn
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self.connect()
        close_after = not self._in_memory
        try:
            for ddl in _TABLE_SCHEMAS.values():
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

    def upsert(self, table_name: str, records: List[Dict[str, Any]]) -> int:
        """Upsert records into a Transmodel table."""
        if table_name not in TRANSMODEL_ENTITIES:
            raise ValueError(f"Unknown table {table_name}")

        model_cls = TRANSMODEL_ENTITIES[table_name]
        conn = self.connect()
        inserted = 0
        try:
            for raw in records:
                obj = model_cls(**raw)
                row_dict = obj.model_dump()
                cols = list(row_dict.keys())
                placeholders = ",".join(["?"] * len(cols))
                safe_cols = [f'"{c}"' if c == "order" else c for c in cols]
                col_str = ",".join(safe_cols)
                sql = f"INSERT OR REPLACE INTO {table_name} ({col_str}) VALUES ({placeholders})"
                conn.execute(sql, [row_dict[c] for c in cols])
                inserted += 1
            conn.execute(
                "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
                ("last_modified", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            if not self._in_memory:
                conn.close()
        return inserted

    def get_records(self, table_name: str) -> List[Dict[str, Any]]:
        """Fetch all records from a table."""
        conn = self.connect()
        try:
            cur = conn.execute(f"SELECT * FROM {table_name}")
            return [dict(row) for row in cur.fetchall()]
        finally:
            if not self._in_memory:
                conn.close()

    def query(
        self,
        table_name: str,
        where: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch records with optional filtering, ordering, and limit."""
        sql = f"SELECT * FROM {table_name}"
        params: list = []
        if where:
            clauses = []
            for col, val in where.items():
                safe_col = f'"{col}"' if col == "order" else col
                clauses.append(f"{safe_col} = ?")
                params.append(val)
            sql += " WHERE " + " AND ".join(clauses)
        if order_by:
            safe_order = f'"{order_by}"' if order_by == "order" else order_by
            sql += f" ORDER BY {safe_order}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
        finally:
            if not self._in_memory:
                conn.close()

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
        sql = f"SELECT COUNT(*) FROM {table_name}"
        params: list = []
        if where:
            clauses = []
            for col, val in where.items():
                safe_col = f'"{col}"' if col == "order" else col
                clauses.append(f"{safe_col} = ?")
                params.append(val)
            sql += " WHERE " + " AND ".join(clauses)
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            return cur.fetchone()[0]
        finally:
            if not self._in_memory:
                conn.close()

    def delete(self, table_name: str, where: Dict[str, Any]) -> int:
        """Delete records matching the where clause. Returns count deleted."""
        if not where:
            raise ValueError("where clause required for delete")
        clauses = []
        params: list = []
        for col, val in where.items():
            safe_col = f'"{col}"' if col == "order" else col
            clauses.append(f"{safe_col} = ?")
            params.append(val)
        sql = f"DELETE FROM {table_name} WHERE " + " AND ".join(clauses)
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        finally:
            if not self._in_memory:
                conn.close()
