"""Tests for the NeTEx importer (round-trip: GTFS -> NeTEx export -> NeTEx import)."""

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.adapters.gtfs_importer import GtfsImporter
from pid_transit.adapters.gtfs_exporter import GtfsExporter
from pid_transit.adapters.netex_exporter import NetexExporter
from pid_transit.adapters.netex_importer import NetexImporter
from tests.conftest import make_gtfs_zip


class TestNetexImporter:
    def test_round_trip_gtfs_to_netex_to_db(self, db, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        GtfsImporter().import_to_db(db, zip_path)

        netex_path = tmp_path / "output.xml"
        NetexExporter(deduplicate_patterns=False).export_from_db(db, netex_path)

        db2 = TransmodelDatabase(":memory:")
        stats = NetexImporter().import_to_db(db2, netex_path)

        assert stats.get("operator", 0) >= 1
        assert stats.get("scheduled_stop_point", 0) >= 1
        assert stats.get("day_type", 0) >= 1
        assert stats.get("line", 0) >= 1
        assert stats.get("journey_pattern", 0) >= 1
        assert stats.get("service_journey", 0) >= 1
        assert stats.get("passing_time", 0) >= 1

    def test_entity_counts_match(self, db, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        GtfsImporter().import_to_db(db, zip_path)

        netex_path = tmp_path / "output.xml"
        NetexExporter(deduplicate_patterns=False).export_from_db(db, netex_path)

        db2 = TransmodelDatabase(":memory:")
        NetexImporter().import_to_db(db2, netex_path)

        assert db.count("operator") == db2.count("operator")
        assert db.count("scheduled_stop_point") == db2.count("scheduled_stop_point")
        assert db.count("line") == db2.count("line")
        assert db.count("journey_pattern") == db2.count("journey_pattern")
        assert db.count("service_journey") == db2.count("service_journey")
        assert db.count("passing_time") == db2.count("passing_time")

    def test_day_offset_handling(self, db, tmp_path):
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
            "calendar": [{"service_id": "WD", "monday": "1", "tuesday": "1",
                          "wednesday": "1", "thursday": "1", "friday": "1",
                          "saturday": "0", "sunday": "0",
                          "start_date": "20260101", "end_date": "20261231"}],
            "trips": [{"trip_id": "T1", "route_id": "R1", "service_id": "WD"}],
            "stop_times": [
                {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1",
                 "arrival_time": "25:30:00", "departure_time": "25:30:00"},
            ],
        }
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(records))
        GtfsImporter().import_to_db(db, zip_path)

        netex_path = tmp_path / "output.xml"
        NetexExporter(deduplicate_patterns=False).export_from_db(db, netex_path)

        db2 = TransmodelDatabase(":memory:")
        NetexImporter().import_to_db(db2, netex_path)

        pt = db2.query("passing_time", where={"service_journey_id": "T1"})
        assert len(pt) >= 1
        assert pt[0]["arrival_time"] == "25:30:00"
