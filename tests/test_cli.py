"""Tests for pid_transit.cli."""

import json

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.core.dataset import TransitDataset
from pid_transit.adapters.gtfs_importer import GtfsImporter
from pid_transit.cli import (
    cmd_import, cmd_export, cmd_validate, cmd_stats, cmd_diff,
    cmd_merge, cmd_rt_readiness, cmd_normalize, cmd_frequencies,
)
from tests.conftest import make_gtfs_zip


class _Args:
    """Simple namespace for simulating argparse output."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _import(records, db_path):
    cmd_import(_Args(source=str(records), format="gtfs",
                     db=str(db_path), timezone="UTC"))


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
                     output=str(out_path), no_shapes=True,
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
                     output=str(out_path), no_shapes=True,
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


class TestCLIValidateSeverity:
    def _bad_db(self, tmp_path):
        """A file-backed DB with a V17 error and a V18 warning."""
        db_path = tmp_path / "bad.db"
        ds = TransitDataset(str(db_path))
        d = ds.db
        d.upsert("operator", [{"id": "OP1", "name": "T", "timezone": "UTC"}])
        d.upsert("scheduled_stop_point", [
            {"id": "S1", "name": "Twin", "lat": 40.0, "lon": -74.0},
            {"id": "S2", "name": "B", "lat": 40.1, "lon": -74.1},
            {"id": "S3", "name": "twin", "lat": 40.00007, "lon": -74.0}])
        d.upsert("line", [{"id": "L1", "operator_id": "OP1", "name": "L",
                           "transport_mode": "bus"}])
        d.upsert("day_type", [{"id": "WD", "monday": True,
                               "start_date": "20260101", "end_date": "20261231"}])
        d.upsert("journey_pattern", [{"id": "JP1", "line_id": "L1", "direction": "outbound"}])
        d.upsert("point_in_journey_pattern", [
            {"journey_pattern_id": "JP1", "stop_point_id": "S1", "order": 1},
            {"journey_pattern_id": "JP1", "stop_point_id": "S2", "order": 2}])
        d.upsert("service_journey", [{"id": "T1", "line_id": "L1",
                                      "journey_pattern_id": "JP1", "day_type_id": "WD",
                                      "departure_time": "08:00:00"}])
        d.upsert("passing_time", [  # swapped → V17 error
            {"service_journey_id": "T1", "stop_point_id": "S2", "order": 1,
             "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            {"service_journey_id": "T1", "stop_point_id": "S1", "order": 2,
             "arrival_time": "08:00:00", "departure_time": "08:00:00"}])
        ds.close()
        return db_path

    def test_validate_prints_severity_and_fails_on_error(self, tmp_path, capsys):
        db_path = self._bad_db(tmp_path)
        with pytest.raises(SystemExit) as exc:
            cmd_validate(_Args(db=str(db_path), json=False, min_severity=None))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "[ERROR]" in out and "FAILED" in out

    def test_validate_json_output(self, tmp_path, capsys):
        db_path = self._bad_db(tmp_path)
        with pytest.raises(SystemExit):
            cmd_validate(_Args(db=str(db_path), json=True, min_severity=None))
        payload = json.loads(capsys.readouterr().out)
        assert payload["is_valid"] is False
        assert any(i["severity"] == "error" for i in payload["issues"])

    def test_validate_min_severity_filters_warnings(self, tmp_path, capsys):
        db_path = self._bad_db(tmp_path)
        with pytest.raises(SystemExit):
            cmd_validate(_Args(db=str(db_path), json=True, min_severity="error"))
        payload = json.loads(capsys.readouterr().out)
        assert all(i["severity"] == "error" for i in payload["issues"])


class TestCLIStatsJson:
    def test_stats_json(self, minimal_gtfs_records, tmp_path, capsys):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db_path = tmp_path / "test.db"
        _import(zip_path, db_path)
        capsys.readouterr()  # discard import output

        cmd_stats(_Args(db=str(db_path), line=None, day_type=None, json=True))
        payload = json.loads(capsys.readouterr().out)
        assert payload["lines"] >= 1


class TestCLIMerge:
    def test_merge_namespaces_sources(self, minimal_gtfs_records, tmp_path, capsys):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        src1, src2 = tmp_path / "a.db", tmp_path / "b.db"
        _import(zip_path, src1)
        _import(zip_path, src2)

        target = tmp_path / "merged.db"
        cmd_merge(_Args(db=str(target), source=[str(src1), str(src2)],
                        namespace=["a", "b"], separator=":",
                        dedupe_stops=False, distance=50.0))
        out = capsys.readouterr().out
        assert "Merged" in out

        db = TransmodelDatabase(target)
        ids = {l["id"] for l in db.get_records("line")}
        assert any(i.startswith("a:") for i in ids)
        assert any(i.startswith("b:") for i in ids)

    def test_merge_rejects_mismatched_pairs(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            cmd_merge(_Args(db=str(tmp_path / "t.db"), source=["x.db"],
                            namespace=[], separator=":",
                            dedupe_stops=False, distance=50.0))
        assert exc.value.code == 1


class TestCLIRtReadiness:
    def test_rt_readiness_runs(self, minimal_gtfs_records, tmp_path, capsys):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db_path = tmp_path / "test.db"
        _import(zip_path, db_path)

        with pytest.raises(SystemExit) as exc:
            cmd_rt_readiness(_Args(db=str(db_path), json=False))
        assert exc.value.code in (0, 1)
        assert capsys.readouterr().out.strip()

    def test_rt_readiness_json(self, minimal_gtfs_records, tmp_path, capsys):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        db_path = tmp_path / "test.db"
        _import(zip_path, db_path)
        capsys.readouterr()  # discard import output

        with pytest.raises(SystemExit):
            cmd_rt_readiness(_Args(db=str(db_path), json=True))
        json.loads(capsys.readouterr().out)  # must be valid JSON


class TestCLINormalize:
    def test_normalize_collapses_patterns(self, tmp_path, capsys):
        records = {
            "agency": [{"agency_id": "A1", "agency_name": "A",
                        "agency_url": "https://x.y", "agency_timezone": "UTC"}],
            "stops": [
                {"stop_id": "S1", "stop_name": "S1", "stop_lat": "40.0",
                 "stop_lon": "-74.0", "location_type": "0"},
                {"stop_id": "S2", "stop_name": "S2", "stop_lat": "40.1",
                 "stop_lon": "-74.1", "location_type": "0"}],
            "routes": [{"route_id": "R1", "agency_id": "A1", "route_short_name": "1",
                        "route_long_name": "R1", "route_type": "3"}],
            "calendar": [{"service_id": "WD", "monday": "1", "tuesday": "1",
                          "wednesday": "1", "thursday": "1", "friday": "1",
                          "saturday": "0", "sunday": "0",
                          "start_date": "20260101", "end_date": "20261231"}],
            "trips": [
                {"trip_id": "T1", "route_id": "R1", "service_id": "WD", "direction_id": "0"},
                {"trip_id": "T2", "route_id": "R1", "service_id": "WD", "direction_id": "0"}],
            "stop_times": [
                {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1",
                 "arrival_time": "08:00:00", "departure_time": "08:00:00"},
                {"trip_id": "T1", "stop_id": "S2", "stop_sequence": "2",
                 "arrival_time": "08:10:00", "departure_time": "08:10:00"},
                {"trip_id": "T2", "stop_id": "S1", "stop_sequence": "1",
                 "arrival_time": "09:00:00", "departure_time": "09:00:00"},
                {"trip_id": "T2", "stop_id": "S2", "stop_sequence": "2",
                 "arrival_time": "09:10:00", "departure_time": "09:10:00"}],
        }
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(records))
        db_path = tmp_path / "test.db"
        _import(zip_path, db_path)

        cmd_normalize(_Args(db=str(db_path), patterns=True))
        out = capsys.readouterr().out
        assert "normalization complete" in out.lower()

        db = TransmodelDatabase(db_path)
        assert db.count("journey_pattern") == 1


class TestCLIFrequencies:
    def _freq_feed(self, tmp_path):
        """A frequency-based GTFS feed: one template trip + a frequencies row."""
        records = {
            "agency": [{"agency_id": "A1", "agency_name": "A",
                        "agency_url": "https://x.y", "agency_timezone": "UTC"}],
            "stops": [
                {"stop_id": "S1", "stop_name": "S1", "stop_lat": "40.0",
                 "stop_lon": "-74.0", "location_type": "0"},
                {"stop_id": "S2", "stop_name": "S2", "stop_lat": "40.1",
                 "stop_lon": "-74.1", "location_type": "0"}],
            "routes": [{"route_id": "R1", "agency_id": "A1", "route_short_name": "1",
                        "route_long_name": "R1", "route_type": "3"}],
            "calendar": [{"service_id": "WD", "monday": "1", "tuesday": "1",
                          "wednesday": "1", "thursday": "1", "friday": "1",
                          "saturday": "0", "sunday": "0",
                          "start_date": "20260101", "end_date": "20261231"}],
            "trips": [{"trip_id": "T1", "route_id": "R1", "service_id": "WD",
                       "direction_id": "0"}],
            "stop_times": [
                {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1",
                 "arrival_time": "06:00:00", "departure_time": "06:00:00"},
                {"trip_id": "T1", "stop_id": "S2", "stop_sequence": "2",
                 "arrival_time": "06:10:00", "departure_time": "06:10:00"}],
            "frequencies": [{"trip_id": "T1", "start_time": "06:00:00",
                             "end_time": "06:30:00", "headway_secs": "600",
                             "exact_times": "1"}],
        }
        zip_path = tmp_path / "freq.zip"
        zip_path.write_bytes(make_gtfs_zip(records))
        db_path = tmp_path / "freq.db"
        _import(zip_path, db_path)
        return db_path

    def test_expand_then_compress_via_cli(self, tmp_path, capsys):
        db_path = self._freq_feed(tmp_path)

        cmd_frequencies(_Args(db=str(db_path), expand=True, compress=False, min_run=3))
        assert "expansion complete" in capsys.readouterr().out.lower()
        db = TransmodelDatabase(db_path)
        assert db.count("service_journey") == 3   # 06:00, 06:10, 06:20
        assert db.count("frequency") == 0

        cmd_frequencies(_Args(db=str(db_path), expand=False, compress=True, min_run=3))
        assert "compression complete" in capsys.readouterr().out.lower()
        db = TransmodelDatabase(db_path)
        assert db.count("frequency") == 1
        assert db.count("service_journey") == 1
