"""Tests for pid_transit.core.database.TransmodelDatabase."""

import pytest

from pid_transit.core.database import TransmodelDatabase


class TestConstruction:
    def test_in_memory(self):
        db = TransmodelDatabase(":memory:")
        assert db.count("operator") == 0

    def test_creates_file(self, tmp_path):
        path = tmp_path / "test.db"
        TransmodelDatabase(path)
        assert path.exists()

    def test_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "a" / "b" / "test.db"
        TransmodelDatabase(nested)
        assert nested.exists()

    def test_idempotent(self, tmp_path):
        path = tmp_path / "test.db"
        db1 = TransmodelDatabase(path)
        db1.upsert("operator", [{
            "id": "OP1", "name": "X", "timezone": "UTC",
        }])
        db2 = TransmodelDatabase(path)
        assert db2.count("operator") == 1


class TestSchemaMigration:
    def test_v30_stop_coords_become_nullable(self, tmp_path):
        import sqlite3
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE scheduled_stop_point (
                id                  TEXT PRIMARY KEY,
                name                TEXT NOT NULL,
                lat                 REAL NOT NULL,
                lon                 REAL NOT NULL,
                stop_area_id        TEXT,
                wheelchair_boarding INTEGER
            )
        """)
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO _meta VALUES ('schema_version', '3.0.0-transmodel')"
        )
        conn.execute(
            "INSERT INTO scheduled_stop_point VALUES ('S1', 'Old', 1.0, 2.0, NULL, NULL)"
        )
        conn.commit()
        conn.close()

        db = TransmodelDatabase(path)
        # existing row survives the rebuild
        assert db.get_one("scheduled_stop_point", where={"id": "S1"})["lat"] == 1.0
        # NULL coordinates are now accepted
        db.upsert("scheduled_stop_point", [{"id": "S2", "name": "No coords"}])
        row = db.get_one("scheduled_stop_point", where={"id": "S2"})
        assert row["lat"] is None


class TestUpsert:
    def test_insert(self, db, sample_operator):
        n = db.upsert("operator", [sample_operator])
        assert n == 1
        assert db.count("operator") == 1

    def test_replace_existing(self, db, sample_operator):
        db.upsert("operator", [sample_operator])
        db.upsert("operator", [{**sample_operator, "name": "Updated"}])
        rows = db.get_records("operator")
        assert len(rows) == 1
        assert rows[0]["name"] == "Updated"

    def test_unknown_table_raises(self, db):
        with pytest.raises(ValueError, match="Unknown table"):
            db.upsert("not_a_table", [{}])

    def test_validates_via_pydantic(self, db):
        with pytest.raises(Exception):
            db.upsert("operator", [{"id": "X"}])

    def test_multiple_records(self, db, sample_stops):
        n = db.upsert("scheduled_stop_point", sample_stops)
        assert n == 2
        assert db.count("scheduled_stop_point") == 2

    def test_empty_records(self, db):
        assert db.upsert("operator", []) == 0

    def test_unvalidated_matches_validated(self, db, sample_operator):
        db.upsert("operator", [sample_operator], validate=True)
        validated = db.get_records("operator")
        db.delete("operator", where={"id": sample_operator["id"]})
        db.upsert("operator", [sample_operator], validate=False)
        assert db.get_records("operator") == validated

    def test_unvalidated_applies_model_defaults(self, db):
        db.upsert("fare_attribute", [{
            "id": "F1", "price": 1.0, "currency_type": "EUR", "payment_method": 0,
        }])
        # fare_rule has defaulted columns the raw dict omits
        db.upsert("fare_rule", [{"fare_id": "F1"}], validate=False)
        row = db.get_records("fare_rule")[0]
        assert row["route_id"] == ""
        assert row["origin_id"] == ""

    def test_unvalidated_missing_required_hits_db_constraint(self, db):
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            db.upsert("operator", [{"id": "X"}], validate=False)


class TestBulkSession:
    def test_commits_on_exit(self, tmp_path, sample_operator):
        path = tmp_path / "test.db"
        db = TransmodelDatabase(path)
        with db.bulk_session():
            db.upsert("operator", [sample_operator])
        db2 = TransmodelDatabase(path)
        assert db2.count("operator") == 1

    def test_reuses_one_connection(self, tmp_path):
        db = TransmodelDatabase(tmp_path / "test.db")
        with db.bulk_session() as conn:
            assert db.connect() is conn
            assert db.connect() is conn
        assert db.connect() is not conn

    def test_bulk_pragmas_on_file_db(self, tmp_path):
        db = TransmodelDatabase(tmp_path / "test.db")
        with db.bulk_session() as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL

    def test_rolls_back_uncommitted_on_error(self, tmp_path, sample_operator):
        path = tmp_path / "test.db"
        db = TransmodelDatabase(path)
        with pytest.raises(RuntimeError):
            with db.bulk_session() as conn:
                conn.execute(
                    "INSERT INTO operator (id, name, timezone) VALUES ('X', 'Y', 'UTC')"
                )
                raise RuntimeError("boom")
        assert TransmodelDatabase(path).count("operator") == 0

    def test_in_memory_passthrough(self, db, sample_operator):
        with db.bulk_session():
            db.upsert("operator", [sample_operator])
        assert db.count("operator") == 1

    def test_nested_sessions_share_connection(self, tmp_path):
        db = TransmodelDatabase(tmp_path / "test.db")
        with db.bulk_session() as outer:
            with db.bulk_session() as inner:
                assert inner is outer
            # inner exit must not release the outer session
            assert db.connect() is outer

    def test_reads_work_inside_session(self, tmp_path, sample_operator):
        db = TransmodelDatabase(tmp_path / "test.db")
        with db.bulk_session():
            db.upsert("operator", [sample_operator])
            assert db.count("operator") == 1
            assert db.get_records("operator")[0]["id"] == sample_operator["id"]


class TestGetRecords:
    def test_empty_table(self, db):
        assert db.get_records("operator") == []

    def test_returns_dicts(self, db, sample_operator):
        db.upsert("operator", [sample_operator])
        records = db.get_records("operator")
        assert len(records) == 1
        assert isinstance(records[0], dict)
        assert records[0]["id"] == "OP1"


class TestQuery:
    def test_where_filter(self, populated_db):
        results = populated_db.query("scheduled_stop_point", where={"id": "S1"})
        assert len(results) == 1
        assert results[0]["name"] == "Stop One"

    def test_order_by(self, populated_db):
        results = populated_db.query(
            "point_in_journey_pattern",
            where={"journey_pattern_id": "JP_T1"},
            order_by="order",
        )
        assert len(results) == 2
        assert results[0]["order"] <= results[1]["order"]

    def test_limit(self, populated_db):
        results = populated_db.query("scheduled_stop_point", limit=1)
        assert len(results) == 1

    def test_no_matches(self, populated_db):
        results = populated_db.query("operator", where={"id": "NOPE"})
        assert results == []


class TestGetOne:
    def test_found(self, populated_db):
        record = populated_db.get_one("operator", where={"id": "OP1"})
        assert record is not None
        assert record["name"] == "Test Agency"

    def test_not_found(self, populated_db):
        record = populated_db.get_one("operator", where={"id": "NOPE"})
        assert record is None


class TestCount:
    def test_total(self, populated_db):
        assert populated_db.count("scheduled_stop_point") == 2

    def test_filtered(self, populated_db):
        assert populated_db.count("scheduled_stop_point", where={"id": "S1"}) == 1
        assert populated_db.count("scheduled_stop_point", where={"id": "NOPE"}) == 0


class TestDelete:
    def test_delete_by_id(self, db, sample_operator):
        db.upsert("operator", [sample_operator])
        n = db.delete("operator", where={"id": "OP1"})
        assert n == 1
        assert db.count("operator") == 0

    def test_delete_no_match(self, populated_db):
        n = populated_db.delete("operator", where={"id": "NOPE"})
        assert n == 0

    def test_delete_requires_where(self, populated_db):
        with pytest.raises(ValueError):
            populated_db.delete("operator", where={})


class TestAggregateCounts:
    def test_count_distinct_does_not_read_the_rows(self, db):
        db.upsert("shape_point", [
            {"shape_id": f"SH{i % 3}", "lat": 40.0 + j, "lon": -8.0, "sequence": j}
            for i in range(3) for j in range(4)
        ])
        assert db.count("shape_point") == 12
        assert db.count_distinct("shape_point", "shape_id") == 3

    def test_count_distinct_refuses_a_column_the_model_has_not_got(self, db):
        with pytest.raises(ValueError, match="nonesuch"):
            db.count_distinct("shape_point", "nonesuch")

    def test_modes_by_stop_point_follows_the_lines_that_call_there(self, db):
        db.upsert("operator", [{"id": "OP", "name": "Op", "timezone": "UTC"}])
        db.upsert("line", [
            {"id": "LM", "operator_id": "OP", "name": "M", "transport_mode": "metro"},
            {"id": "LB", "operator_id": "OP", "name": "B", "transport_mode": "bus"},
        ])
        db.upsert("scheduled_stop_point", [
            {"id": "SM", "name": "M"}, {"id": "SB", "name": "B"},
            {"id": "SX", "name": "Unserved"},
        ])
        db.upsert("journey_pattern", [{"id": "JM", "line_id": "LM"},
                                      {"id": "JB", "line_id": "LB"}])
        db.upsert("point_in_journey_pattern", [
            {"journey_pattern_id": "JM", "stop_point_id": "SM", "order": 0},
            {"journey_pattern_id": "JB", "stop_point_id": "SB", "order": 0},
        ])
        # A stop no pattern calls at is absent rather than guessed at.
        assert db.transport_modes_by_stop_point() == {"SM": "metro", "SB": "bus"}

    def test_a_stop_served_by_two_modes_takes_the_busier_one(self, db):
        db.upsert("operator", [{"id": "OP", "name": "Op", "timezone": "UTC"}])
        db.upsert("line", [
            {"id": "LM", "operator_id": "OP", "name": "M", "transport_mode": "metro"},
            {"id": "LB", "operator_id": "OP", "name": "B", "transport_mode": "bus"},
        ])
        db.upsert("scheduled_stop_point", [{"id": "S", "name": "Interchange"}])
        db.upsert("journey_pattern", [{"id": "JM", "line_id": "LM"},
                                      {"id": "JB1", "line_id": "LB"},
                                      {"id": "JB2", "line_id": "LB"}])
        db.upsert("point_in_journey_pattern", [
            {"journey_pattern_id": "JM", "stop_point_id": "S", "order": 0},
            {"journey_pattern_id": "JB1", "stop_point_id": "S", "order": 0},
            {"journey_pattern_id": "JB2", "stop_point_id": "S", "order": 0},
        ])
        assert db.transport_modes_by_stop_point() == {"S": "bus"}
