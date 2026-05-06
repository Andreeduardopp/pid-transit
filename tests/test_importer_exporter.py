"""Round-trip tests for the importer and exporter."""

import io
import zipfile

import pytest

from pid_transit.legacy import (
    GtfsDatabase,
    GtfsImportError,
    ImportMode,
    export_zip,
    import_zip,
    preview_zip,
)


# ─── Helpers ─────────────────────────────────────────────────────────────

def _make_zip(records: dict[str, list[dict]]) -> bytes:
    """Build an in-memory GTFS zip from a mapping of table -> row dicts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table, rows in records.items():
            if not rows:
                continue
            cols = list(rows[0].keys())
            lines = [",".join(cols)]
            for row in rows:
                lines.append(",".join(str(row[c]) for c in cols))
            zf.writestr(f"{table}.txt", "\r\n".join(lines))
    return buf.getvalue()


# ─── Preview ─────────────────────────────────────────────────────────────

class TestPreview:
    def test_valid_feed(self, minimal_feed_records):
        data = _make_zip(minimal_feed_records)
        preview = preview_zip(io.BytesIO(data))
        assert preview.is_valid
        assert preview.recognised_tables["agency"] == 1
        assert preview.recognised_tables["stop_times"] == 2
        assert preview.missing_required == []

    def test_missing_required_file(self, minimal_feed_records):
        records = dict(minimal_feed_records)
        del records["stop_times"]
        data = _make_zip(records)
        preview = preview_zip(io.BytesIO(data))
        assert not preview.is_valid
        assert "stop_times" in preview.missing_required

    def test_not_a_zip(self, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        preview = preview_zip(bad)
        assert not preview.is_valid
        assert preview.errors


# ─── Import ──────────────────────────────────────────────────────────────

class TestImport:
    def test_replace_into_empty_db(self, db, minimal_feed_records):
        data = _make_zip(minimal_feed_records)
        result = import_zip(db, io.BytesIO(data), mode=ImportMode.REPLACE)
        assert result.total_failed == 0
        assert db.count("agency") == 1
        assert db.count("stops") == 2
        assert db.count("stop_times") == 2

    def test_merge_adds_new_records(self, populated_db, minimal_feed_records):
        # Add a second agency and re-import
        extra = dict(minimal_feed_records)
        extra["agency"] = extra["agency"] + [{
            "agency_id": "A2", "agency_name": "Second",
            "agency_url": "https://y.com", "agency_timezone": "UTC",
        }]
        data = _make_zip(extra)
        result = import_zip(populated_db, io.BytesIO(data), mode=ImportMode.MERGE)
        assert result.total_failed == 0
        assert populated_db.count("agency") == 2

    def test_abort_if_not_empty_raises(self, populated_db, minimal_feed_records):
        data = _make_zip(minimal_feed_records)
        with pytest.raises(GtfsImportError):
            import_zip(
                populated_db,
                io.BytesIO(data),
                mode=ImportMode.ABORT_IF_NOT_EMPTY,
            )

    def test_merge_partial_requires_populated_db(self, db, minimal_feed_records):
        # Single-table archive — missing required files
        partial = {"agency": minimal_feed_records["agency"]}
        data = _make_zip(partial)
        with pytest.raises(GtfsImportError):
            import_zip(db, io.BytesIO(data), mode=ImportMode.MERGE_PARTIAL)

    def test_merge_partial_into_populated(self, populated_db, minimal_feed_records):
        partial = {"agency": minimal_feed_records["agency"] + [{
            "agency_id": "A2", "agency_name": "Second",
            "agency_url": "https://y.com", "agency_timezone": "UTC",
        }]}
        data = _make_zip(partial)
        result = import_zip(
            populated_db,
            io.BytesIO(data),
            mode=ImportMode.MERGE_PARTIAL,
        )
        assert result.total_failed == 0
        assert populated_db.count("agency") == 2


# ─── Export ──────────────────────────────────────────────────────────────

class TestExport:
    def test_refuses_empty_db_with_validate(self, db, tmp_path):
        result = export_zip(db, tmp_path / "out.zip")
        assert not result.success
        assert any("empty" in e.lower() for e in result.errors)

    def test_happy_path(self, populated_db, tmp_path):
        out = tmp_path / "out.zip"
        result = export_zip(populated_db, out)
        assert result.success
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "agency.txt" in names
        assert "stop_times.txt" in names

    def test_validate_false_skips_check(self, db, tmp_path):
        # empty db + validate=False => no files to write, but no error gate
        result = export_zip(db, tmp_path / "out.zip", validate=False)
        # Empty DB yields "no tables had records" error, not a validation error
        assert not result.success


# ─── Round trip ─────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_import_then_export_preserves_counts(
        self, db, tmp_path, minimal_feed_records
    ):
        data = _make_zip(minimal_feed_records)
        import_zip(db, io.BytesIO(data), mode=ImportMode.REPLACE)

        out = tmp_path / "exported.zip"
        res = export_zip(db, out)
        assert res.success

        # Import the exported zip into a fresh DB and compare counts
        db2 = GtfsDatabase(tmp_path / "db2.db")
        import_zip(db2, out, mode=ImportMode.REPLACE)
        for table in ("agency", "stops", "routes", "trips", "stop_times", "calendar"):
            assert db.count(table) == db2.count(table), f"mismatch for {table}"
