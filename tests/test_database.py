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
