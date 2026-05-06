"""Tests for pid_transit.legacy.database.GtfsDatabase."""

from pathlib import Path

import pytest

from pid_transit.legacy import GtfsDatabase


class TestConstruction:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "a.db"
        db = GtfsDatabase(path)
        assert db.exists()
        assert path.exists()

    def test_idempotent(self, tmp_path):
        path = tmp_path / "a.db"
        GtfsDatabase(path)
        # Second construction should not wipe existing data
        db = GtfsDatabase(path)
        db.upsert("agency", [{
            "agency_id": "A1", "agency_name": "X",
            "agency_url": "https://x.com", "agency_timezone": "UTC",
        }])
        db2 = GtfsDatabase(path)
        assert db2.count("agency") == 1

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "deeper" / "x.db"
        db = GtfsDatabase(nested)
        assert db.exists()

    def test_from_slug_uses_root(self, tmp_path):
        db = GtfsDatabase.from_slug("myfeed", root=tmp_path)
        assert db.db_path == tmp_path / "myfeed.db"
        assert db.exists()

    def test_from_slug_default_root_is_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = GtfsDatabase.from_slug("myfeed")
        try:
            assert db.db_path.resolve() == (tmp_path / "myfeed.db").resolve()
        finally:
            db.db_path.unlink(missing_ok=True)


class TestUpsert:
    def test_valid_insert(self, db):
        result = db.upsert("agency", [{
            "agency_id": "A1", "agency_name": "X",
            "agency_url": "https://x.com", "agency_timezone": "UTC",
        }])
        assert result.inserted == 1
        assert result.failed == 0
        assert db.count("agency") == 1

    def test_replaces_existing(self, db):
        row = {
            "agency_id": "A1", "agency_name": "X",
            "agency_url": "https://x.com", "agency_timezone": "UTC",
        }
        db.upsert("agency", [row])
        db.upsert("agency", [{**row, "agency_name": "Y"}])
        rows = db.get_records("agency")
        assert len(rows) == 1
        assert rows[0]["agency_name"] == "Y"

    def test_invalid_record_fails_but_valid_commits(self, db):
        result = db.upsert("agency", [
            {"agency_id": "A1", "agency_name": "X",
             "agency_url": "https://x.com", "agency_timezone": "UTC"},
            {"agency_id": "A2"},  # missing required fields
        ])
        assert result.inserted == 1
        assert result.failed == 1
        assert len(result.errors) == 1

    def test_unknown_table(self, db):
        result = db.upsert("not_a_table", [{}])
        assert result.inserted == 0
        assert result.failed == 1


class TestRead:
    def test_get_records_pagination(self, db):
        db.upsert("stops", [
            {"stop_id": f"S{i}", "stop_name": f"n{i}",
             "stop_lat": 40.0, "stop_lon": -74.0}
            for i in range(10)
        ])
        page = db.get_records("stops", limit=3, offset=5)
        assert len(page) == 3

    def test_count(self, db):
        db.upsert("stops", [
            {"stop_id": f"S{i}", "stop_name": "x",
             "stop_lat": 40.0, "stop_lon": -74.0}
            for i in range(4)
        ])
        assert db.count("stops") == 4


class TestDelete:
    def test_delete_by_single_pk(self, db):
        db.upsert("agency", [{
            "agency_id": "A1", "agency_name": "X",
            "agency_url": "https://x.com", "agency_timezone": "UTC",
        }])
        n = db.delete("agency", ["A1"])
        assert n == 1
        assert db.count("agency") == 0

    def test_clear_table(self, db):
        db.upsert("stops", [
            {"stop_id": "S1", "stop_name": "x",
             "stop_lat": 40.0, "stop_lon": -74.0},
        ])
        n = db.clear("stops")
        assert n == 1

    def test_clear_all_respects_fk_order(self, populated_db):
        deleted = populated_db.clear_all()
        assert populated_db.count("stop_times") == 0
        assert populated_db.count("agency") == 0
        # clear_all always reports every known table (even if the count is 0)
        assert "stop_times" in deleted


class TestIntegrity:
    def test_clean_on_populated_db(self, populated_db):
        report = populated_db.check_integrity()
        assert report.is_clean
        assert report.violations == []

    def test_detects_orphan_when_fk_violated_via_raw_insert(self, db):
        # Bypass the class to insert an orphan row via raw connection
        conn = db.connect()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO routes (route_id, agency_id, route_type) "
            "VALUES (?, ?, ?)",
            ("R_orphan", "NOT_REAL", 3),
        )
        conn.commit()
        conn.close()
        report = db.check_integrity()
        assert not report.is_clean
        assert any(v.table == "routes" for v in report.violations)


class TestSummary:
    def test_empty(self, db):
        s = db.summary()
        assert s["exists"]
        assert s["total_records"] == 0
        assert "stops" in s["empty_tables"]

    def test_populated(self, populated_db):
        s = populated_db.summary()
        assert s["total_records"] > 0
        assert "agency" in s["populated_tables"]
