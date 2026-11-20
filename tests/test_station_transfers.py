"""Transfers whose ends are stations (GTFS location_type 1).

GTFS lets transfers.txt name a station on either side, and real feeds do it
(Euskotren lists station-to-station walking times). The model keeps stations
as stop areas, not as scheduled stop points, so a transfer end may live in
either table.
"""

import sqlite3

import pytest

from pid_transit.core.database import TransmodelDatabase, SCHEMA_VERSION
from pid_transit.core.dataset import TransitDataset
from pid_transit.core.validator import validate_transmodel
from pid_transit.adapters.gtfs_importer import GtfsImporter
from pid_transit.adapters.gtfs_exporter import GtfsExporter
from pid_transit.adapters.netex_exporter import NetexExporter
from pid_transit.analytics.diff import FeedDiffer
from tests.conftest import make_gtfs_zip
from tests.test_roundtrip import _read_csv


def _records():
    stop = {"stop_lat": "43.0", "stop_lon": "-2.0", "parent_station": "",
            "location_type": "0"}
    return {
        "agency": [{"agency_id": "A1", "agency_name": "Rail",
                    "agency_url": "https://example.com",
                    "agency_timezone": "Europe/Madrid"}],
        "stops": [
            {**stop, "stop_id": "ST1", "stop_name": "North", "location_type": "1"},
            {**stop, "stop_id": "ST2", "stop_name": "South", "location_type": "1"},
            {**stop, "stop_id": "P1", "stop_name": "North 1", "parent_station": "ST1"},
            {**stop, "stop_id": "P2", "stop_name": "South 1", "parent_station": "ST2"},
        ],
        "routes": [{"route_id": "R1", "agency_id": "A1", "route_short_name": "1",
                    "route_long_name": "Line 1", "route_type": "2"}],
        "calendar": [{"service_id": "WK", "monday": "1", "tuesday": "1",
                      "wednesday": "1", "thursday": "1", "friday": "1",
                      "saturday": "0", "sunday": "0",
                      "start_date": "20260101", "end_date": "20261231"}],
        "trips": [{"trip_id": "T1", "route_id": "R1", "service_id": "WK"}],
        "stop_times": [
            {"trip_id": "T1", "stop_id": "P1", "stop_sequence": "1",
             "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            {"trip_id": "T1", "stop_id": "P2", "stop_sequence": "2",
             "arrival_time": "08:10:00", "departure_time": "08:10:00"},
        ],
        "transfers": [
            {"from_stop_id": "ST1", "to_stop_id": "ST1",
             "transfer_type": "2", "min_transfer_time": "60"},
            {"from_stop_id": "ST1", "to_stop_id": "ST2",
             "transfer_type": "2", "min_transfer_time": "120"},
            {"from_stop_id": "P1", "to_stop_id": "P2",
             "transfer_type": "2", "min_transfer_time": "180"},
        ],
    }


@pytest.fixture
def feed(tmp_path):
    path = tmp_path / "stations.zip"
    path.write_bytes(make_gtfs_zip(_records()))
    return path


def _import(source):
    ds = TransitDataset(":memory:")
    ds.import_data(GtfsImporter(), source)
    return ds


class TestImport:
    def test_station_transfers_are_imported(self, feed):
        ds = _import(feed)
        assert ds.db.count("transfer") == 3

    def test_gtfs_round_trip_keeps_them(self, feed, tmp_path):
        first = _import(feed)
        out = tmp_path / "out.zip"
        first.export_data(GtfsExporter(), out)
        rows = {(r["from_stop_id"], r["to_stop_id"]): r["min_transfer_time"]
                for r in _read_csv(out, "transfers.txt")}
        assert rows == {("ST1", "ST1"): "60", ("ST1", "ST2"): "120",
                        ("P1", "P2"): "180"}
        assert not FeedDiffer(first, _import(out)).diff().has_changes


class TestValidation:
    def test_valid_station_transfers_raise_no_issue(self, feed):
        report = validate_transmodel(_import(feed).db)
        assert not [i for i in report.issues if i.entity_type == "transfer"]

    def test_a_transfer_to_an_unknown_stop_is_reported(self, feed):
        ds = _import(feed)
        ds.db.upsert("transfer", [{"from_stop_id": "ST1", "to_stop_id": "NOPE",
                                   "transfer_type": 0}])
        issues = [i for i in validate_transmodel(ds.db).issues
                  if i.entity_type == "transfer"]
        assert len(issues) == 1
        assert issues[0].issue_type == "orphan_stop_reference"
        assert "NOPE" in issues[0].message


class TestNetexExport:
    """A NeTEx Connection joins two scheduled stop points. Under a profile
    that writes the station structure (epip-stations), a transfer with a
    station end becomes a SiteConnection whose end is the station's
    StopPlace; a profile that writes no station still counts it as lost."""

    def test_station_transfers_are_site_connections(self, feed, tmp_path):
        ds = _import(feed)
        out = tmp_path / "feed.xml"
        losses = NetexExporter(profile="epip-stations").export_from_db(ds.db, out)
        xml = out.read_text(encoding="utf-8")
        connections = xml.split("<connections>")[1].split("</connections>")[0]
        assert connections.count("<Connection ") == 1
        assert connections.count("<SiteConnection ") == 2
        assert '<StopPlaceRef ref="ST1"' in connections
        assert "unsupported_transfer_station_end" not in losses

    def test_netex_round_trip_keeps_every_transfer(self, feed, tmp_path):
        from pid_transit.adapters.netex_importer import NetexImporter
        ds = _import(feed)
        out = tmp_path / "feed.xml"
        NetexExporter(profile="epip-stations").export_from_db(ds.db, out)
        back = TransitDataset(":memory:")
        stats = back.import_data(NetexImporter(), out)
        key = lambda r: (r["from_stop_id"], r["to_stop_id"], r["transfer_type"],
                         r["min_transfer_time"])
        assert sorted(map(key, back.db.get_records("transfer"))) == \
            sorted(map(key, ds.db.get_records("transfer")))
        assert not any(k.startswith(("skipped_", "dropped_")) for k in stats)

    def test_epip_writes_no_station_and_counts_them(self, feed, tmp_path):
        # The EPIP StopPlace admits quays only, so there is no StopPlace for
        # a station end to reference.
        ds = _import(feed)
        out = tmp_path / "epip.xml"
        losses = NetexExporter(profile="epip").export_from_db(ds.db, out)
        connections = out.read_text(encoding="utf-8").split(
            "<connections>")[1].split("</connections>")[0]
        assert "<SiteConnection " not in connections
        assert connections.count("<Connection ") == 1
        assert losses["unsupported_transfer_station_end"] == 2

    def test_a_profile_without_connections_still_counts_them(self, feed, tmp_path):
        ds = _import(feed)
        losses = NetexExporter(profile="pt").export_from_db(ds.db, tmp_path / "pt.xml")
        assert losses["unsupported_transfer"] == 3


class TestMigration:
    def test_old_transfer_table_loses_its_foreign_keys(self, tmp_path):
        path = tmp_path / "old.db"
        TransmodelDatabase(path)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE transfer")
        conn.execute("""
            CREATE TABLE transfer (
                from_stop_id TEXT NOT NULL REFERENCES scheduled_stop_point(id),
                to_stop_id   TEXT NOT NULL REFERENCES scheduled_stop_point(id),
                transfer_type INTEGER NOT NULL DEFAULT 0,
                min_transfer_time INTEGER, extras TEXT,
                PRIMARY KEY (from_stop_id, to_stop_id))""")
        conn.execute("INSERT INTO scheduled_stop_point (id, name) VALUES ('S1', 'a')")
        conn.execute("INSERT INTO transfer VALUES ('S1', 'S1', 2, 60, NULL)")
        conn.execute("UPDATE _meta SET value = '3.2.0-transmodel' "
                     "WHERE key = 'schema_version'")
        conn.commit()
        conn.close()

        db = TransmodelDatabase(path)
        conn = db.connect()
        assert conn.execute("PRAGMA foreign_key_list(transfer)").fetchall() == []
        assert conn.execute("SELECT value FROM _meta WHERE key='schema_version'"
                            ).fetchone()[0] == SCHEMA_VERSION
        conn.close()
        assert db.get_one("transfer", where={"from_stop_id": "S1"})["min_transfer_time"] == 60
        db.upsert("transfer", [{"from_stop_id": "ST9", "to_stop_id": "ST9",
                                "transfer_type": 2}])
        assert db.count("transfer") == 2
