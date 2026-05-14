"""Round-trip tests for GTFS importer and exporter."""

import io
import zipfile

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.adapters.gtfs_importer import GtfsImporter
from pid_transit.adapters.gtfs_exporter import GtfsExporter
from tests.conftest import make_gtfs_zip


class TestGtfsImporter:
    def test_import_minimal_feed(self, db, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        importer = GtfsImporter()
        stats = importer.import_to_db(db, zip_path)
        assert stats["operator"] == 1
        assert stats["line"] == 1
        assert stats["scheduled_stop_point"] == 2
        assert stats["day_type"] == 1
        assert stats["journey_pattern"] == 1
        assert stats["service_journey"] == 1
        assert stats["point_in_journey_pattern"] == 2
        assert stats["passing_time"] == 2

    def test_departure_time_backfill(self, db, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        importer = GtfsImporter()
        importer.import_to_db(db, zip_path)
        sj = db.get_one("service_journey", where={"id": "T1"})
        assert sj["departure_time"] == "08:00:00"

    def test_direction_mapping(self, db, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        importer = GtfsImporter()
        importer.import_to_db(db, zip_path)
        jp = db.get_one("journey_pattern", where={"id": "JP_T1"})
        assert jp["direction"] == "outbound"

    def test_skips_parent_stations(self, db, minimal_gtfs_records, tmp_path):
        records = dict(minimal_gtfs_records)
        records["stops"] = records["stops"] + [
            {"stop_id": "STATION1", "stop_name": "Station",
             "stop_lat": "40.0", "stop_lon": "-74.0", "location_type": "1"},
        ]
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(records))
        importer = GtfsImporter()
        importer.import_to_db(db, zip_path)
        assert db.count("scheduled_stop_point") == 2

    def test_calendar_dates_only_creates_placeholder_day_types(self, db, tmp_path):
        records = {
            "agency": [{"agency_id": "A1", "agency_name": "Test",
                        "agency_url": "https://x.com", "agency_timezone": "UTC"}],
            "stops": [
                {"stop_id": "S1", "stop_name": "Stop",
                 "stop_lat": "40.0", "stop_lon": "-74.0", "location_type": "0"},
            ],
            "routes": [{"route_id": "R1", "agency_id": "A1",
                        "route_short_name": "1", "route_long_name": "Route",
                        "route_type": "3"}],
            "calendar_dates": [
                {"service_id": "SVC1", "date": "20260115", "exception_type": "1"},
                {"service_id": "SVC1", "date": "20260116", "exception_type": "1"},
            ],
            "trips": [{"trip_id": "T1", "route_id": "R1", "service_id": "SVC1"}],
            "stop_times": [
                {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1",
                 "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            ],
        }
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(records))
        importer = GtfsImporter()
        importer.import_to_db(db, zip_path)
        dt = db.get_one("day_type", where={"id": "SVC1"})
        assert dt is not None
        assert dt["monday"] == 0
        assert db.count("operating_day_exception") == 2


class TestGtfsExporter:
    def test_export_produces_valid_zip(self, populated_db, tmp_path):
        out = tmp_path / "export.zip"
        exporter = GtfsExporter()
        exporter.export_from_db(populated_db, out)
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "agency.txt" in names
        assert "routes.txt" in names
        assert "stops.txt" in names
        assert "calendar.txt" in names
        assert "trips.txt" in names
        assert "stop_times.txt" in names

    def test_export_record_counts(self, populated_db, tmp_path):
        out = tmp_path / "export.zip"
        exporter = GtfsExporter()
        exporter.export_from_db(populated_db, out)
        with zipfile.ZipFile(out) as zf:
            agency_lines = zf.read("agency.txt").decode().strip().splitlines()
            stops_lines = zf.read("stops.txt").decode().strip().splitlines()
        assert len(agency_lines) == 2  # header + 1 record
        assert len(stops_lines) == 3   # header + 2 records


class TestRoundTrip:
    def test_import_export_preserves_counts(self, db, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "input.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        importer = GtfsImporter()
        importer.import_to_db(db, zip_path)

        out = tmp_path / "exported.zip"
        exporter = GtfsExporter()
        exporter.export_from_db(db, out)

        db2 = TransmodelDatabase(":memory:")
        importer2 = GtfsImporter()
        importer2.import_to_db(db2, out)

        for table in ("operator", "line", "scheduled_stop_point",
                      "day_type", "service_journey", "passing_time"):
            assert db.count(table) == db2.count(table), f"mismatch for {table}"

    def test_round_trip_preserves_departure_time(self, db, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "input.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        importer = GtfsImporter()
        importer.import_to_db(db, zip_path)

        out = tmp_path / "exported.zip"
        GtfsExporter().export_from_db(db, out)

        db2 = TransmodelDatabase(":memory:")
        GtfsImporter().import_to_db(db2, out)

        sj1 = db.get_one("service_journey", where={"id": "T1"})
        sj2 = db2.get_one("service_journey", where={"id": "T1"})
        assert sj1["departure_time"] == sj2["departure_time"]
