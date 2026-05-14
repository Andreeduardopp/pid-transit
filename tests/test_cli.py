"""Tests for pid_transit.cli."""

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.core.dataset import TransitDataset
from pid_transit.adapters.gtfs_importer import GtfsImporter
from pid_transit.cli import cmd_import, cmd_export, cmd_validate, cmd_stats, cmd_diff
from tests.conftest import make_gtfs_zip


class _Args:
    """Simple namespace for simulating argparse output."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestCLIImport:
    def test_import_gtfs(self, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db_path = tmp_path / "test.db"

        args = _Args(source=str(zip_path), format="gtfs",
                     db=str(db_path), timezone="UTC")
        cmd_import(args)

        db = TransmodelDatabase(db_path)
        assert db.count("operator") >= 1
        assert db.count("line") >= 1


class TestCLIExport:
    def test_export_gtfs(self, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db_path = tmp_path / "test.db"
        cmd_import(_Args(source=str(zip_path), format="gtfs",
                         db=str(db_path), timezone="UTC"))

        out_path = tmp_path / "export.zip"
        args = _Args(db=str(db_path), format="gtfs",
                     output=str(out_path), include_shapes=False,
                     no_dedup=False, compress=False)
        cmd_export(args)
        assert out_path.exists()

    def test_export_netex(self, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db_path = tmp_path / "test.db"
        cmd_import(_Args(source=str(zip_path), format="gtfs",
                         db=str(db_path), timezone="UTC"))

        out_path = tmp_path / "export.xml"
        args = _Args(db=str(db_path), format="netex",
                     output=str(out_path), include_shapes=False,
                     no_dedup=False, compress=False)
        cmd_export(args)
        assert out_path.exists()


class TestCLIValidate:
    def test_validate_populated(self, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db_path = tmp_path / "test.db"
        cmd_import(_Args(source=str(zip_path), format="gtfs",
                         db=str(db_path), timezone="UTC"))

        args = _Args(db=str(db_path))
        # Should not raise
        try:
            cmd_validate(args)
        except SystemExit as e:
            assert e.code == 0


class TestCLIStats:
    def test_stats_summary(self, minimal_gtfs_records, tmp_path, capsys):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db_path = tmp_path / "test.db"
        cmd_import(_Args(source=str(zip_path), format="gtfs",
                         db=str(db_path), timezone="UTC"))

        args = _Args(db=str(db_path), line=None, day_type=None)
        cmd_stats(args)
        captured = capsys.readouterr()
        assert "Dataset Summary" in captured.out


class TestCLIDiff:
    def test_diff_identical(self, minimal_gtfs_records, tmp_path, capsys):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db1 = tmp_path / "db1.db"
        db2 = tmp_path / "db2.db"
        cmd_import(_Args(source=str(zip_path), format="gtfs",
                         db=str(db1), timezone="UTC"))
        cmd_import(_Args(source=str(zip_path), format="gtfs",
                         db=str(db2), timezone="UTC"))

        args = _Args(base=str(db1), target=str(db2), output=None)
        cmd_diff(args)
        captured = capsys.readouterr()
        assert "Feed Comparison Report" in captured.out

    def test_diff_to_file(self, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db1 = tmp_path / "db1.db"
        db2 = tmp_path / "db2.db"
        cmd_import(_Args(source=str(zip_path), format="gtfs",
                         db=str(db1), timezone="UTC"))
        cmd_import(_Args(source=str(zip_path), format="gtfs",
                         db=str(db2), timezone="UTC"))

        out = tmp_path / "report.md"
        args = _Args(base=str(db1), target=str(db2), output=str(out))
        cmd_diff(args)
        assert out.exists()
        assert "Feed Comparison Report" in out.read_text()
