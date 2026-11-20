"""Round-trip property tests (roadmap task 2).

Machine-checks the central claim of docs/formalization.md §1:
export ∘ import ≅ id, using FeedDiffer as the implementation of ≅
(two datasets are equivalent iff their diff report is empty).
"""

import csv
import io
import json
import sqlite3
import sys
import zipfile
from typing import Any, Dict, List

import pytest

from pid_transit.core.database import TransmodelDatabase, SCHEMA_VERSION
from pid_transit.core.dataset import TransitDataset
from pid_transit.adapters.gtfs_importer import GtfsImporter
from pid_transit.adapters.gtfs_exporter import GtfsExporter
from pid_transit.analytics.diff import FeedDiffer
from tests.conftest import make_gtfs_zip


# ---------------------------------------------------------------------------
# Rich fixture: exercises every mapped field, extras, and extended route types
# ---------------------------------------------------------------------------

@pytest.fixture
def rich_gtfs_records() -> Dict[str, List[Dict[str, Any]]]:
    return {
        "agency": [{
            "agency_id": "A1",
            "agency_name": "Test Agency",
            "agency_url": "https://example.com",
            "agency_timezone": "UTC",
            "agency_lang": "en",
            "agency_phone": "555-0100",
            "agency_fare_url": "https://example.com/fares",  # -> extras
        }],
        "levels": [
            {"level_id": "L0", "level_index": "0", "level_name": "Ground"},
        ],
        "stops": [
            {"stop_id": "S1", "stop_name": "Stop 1",
             "stop_lat": "40.0", "stop_lon": "-74.0", "location_type": "0",
             "parent_station": "STATION1", "wheelchair_boarding": "1",
             "stop_code": "1001", "platform_code": "A", "zone_id": "Z1",
             "stop_desc": "Main platform", "level_id": "",
             "tts_stop_name": "Stop One"},  # tts_stop_name -> extras
            {"stop_id": "S2", "stop_name": "Stop 2",
             "stop_lat": "40.1", "stop_lon": "-74.1", "location_type": "0",
             "parent_station": "", "wheelchair_boarding": "",
             "stop_code": "1002", "platform_code": "", "zone_id": "Z2",
             "stop_desc": "", "level_id": "", "tts_stop_name": ""},
            {"stop_id": "STATION1", "stop_name": "Central Station",
             "stop_lat": "40.0", "stop_lon": "-74.0", "location_type": "1",
             "parent_station": "", "wheelchair_boarding": "1",
             "stop_code": "", "platform_code": "", "zone_id": "",
             "stop_desc": "", "level_id": "L0", "tts_stop_name": ""},
        ],
        "routes": [
            {"route_id": "R1", "agency_id": "A1",
             "route_short_name": "1", "route_long_name": "Route 1",
             "route_type": "3", "route_color": "FF0000",
             "route_text_color": "FFFFFF", "route_desc": "Main line",
             "route_url": "https://example.com/r1", "route_sort_order": "10",
             "network_id": "N1"},  # network_id -> extras
            {"route_id": "R700", "agency_id": "A1",
             "route_short_name": "X7", "route_long_name": "Express Coach",
             "route_type": "700",  # extended type: must re-export as 700
             "route_color": "", "route_text_color": "", "route_desc": "",
             "route_url": "", "route_sort_order": "", "network_id": ""},
        ],
        "calendar": [{
            "service_id": "WEEKDAY",
            "monday": "1", "tuesday": "1", "wednesday": "1",
            "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
            "start_date": "20260101", "end_date": "20261231",
        }],
        "calendar_dates": [
            {"service_id": "WEEKDAY", "date": "20260704", "exception_type": "2"},
        ],
        "trips": [
            {"trip_id": "T1", "route_id": "R1", "service_id": "WEEKDAY",
             "direction_id": "0", "trip_headsign": "Downtown",
             "trip_short_name": "101", "block_id": "B1",
             "wheelchair_accessible": "1", "bikes_allowed": "2",
             "shape_id": "SH1"},
            {"trip_id": "T2", "route_id": "R700", "service_id": "WEEKDAY",
             "direction_id": "1", "trip_headsign": "", "trip_short_name": "",
             "block_id": "", "wheelchair_accessible": "", "bikes_allowed": "",
             "shape_id": ""},
        ],
        "stop_times": [
            {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1",
             "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "pickup_type": "0", "drop_off_type": "1", "timepoint": "1",
             "stop_headsign": "Downtown via Main",
             "shape_dist_traveled": "0.0"},
            {"trip_id": "T1", "stop_id": "S2", "stop_sequence": "2",
             "arrival_time": "08:10:00", "departure_time": "08:11:00",
             "pickup_type": "2", "drop_off_type": "0", "timepoint": "0",
             "stop_headsign": "", "shape_dist_traveled": "1234.5"},
            # overnight service: times >= 24:00:00 must survive
            {"trip_id": "T2", "stop_id": "S1", "stop_sequence": "1",
             "arrival_time": "24:50:00", "departure_time": "24:50:00",
             "pickup_type": "", "drop_off_type": "", "timepoint": "",
             "stop_headsign": "", "shape_dist_traveled": ""},
            {"trip_id": "T2", "stop_id": "S2", "stop_sequence": "2",
             "arrival_time": "25:05:00", "departure_time": "25:05:00",
             "pickup_type": "", "drop_off_type": "", "timepoint": "",
             "stop_headsign": "", "shape_dist_traveled": ""},
        ],
        "shapes": [
            {"shape_id": "SH1", "shape_pt_lat": "40.0", "shape_pt_lon": "-74.0",
             "shape_pt_sequence": "1", "shape_dist_traveled": "0.0"},
            {"shape_id": "SH1", "shape_pt_lat": "40.1", "shape_pt_lon": "-74.1",
             "shape_pt_sequence": "2", "shape_dist_traveled": "1234.5"},
        ],
        "frequencies": [
            {"trip_id": "T1", "start_time": "06:00:00", "end_time": "09:00:00",
             "headway_secs": "600", "exact_times": "1"},
        ],
        "transfers": [
            {"from_stop_id": "S1", "to_stop_id": "S2",
             "transfer_type": "2", "min_transfer_time": "120"},
        ],
        "fare_attributes": [
            {"fare_id": "F1", "price": "2.50", "currency_type": "EUR",
             "payment_method": "1", "transfers": "0", "agency_id": "A1",
             "transfer_duration": "3600"},
        ],
        "fare_rules": [
            {"fare_id": "F1", "route_id": "R1", "origin_id": "Z1",
             "destination_id": "Z2", "contains_id": ""},
        ],
        "translations": [
            {"table_name": "stops", "field_name": "stop_name",
             "language": "pt", "translation": "Paragem 1",
             "record_id": "S1", "record_sub_id": "", "field_value": ""},
        ],
        "attributions": [
            {"attribution_id": "AT1", "organization_name": "Data Co",
             "is_producer": "1", "is_operator": "", "is_authority": "",
             "attribution_url": "https://example.com/data",
             "attribution_email": "", "attribution_phone": ""},
        ],
        "feed_info": [{
            "feed_publisher_name": "Test Publisher",
            "feed_publisher_url": "https://example.com",
            "feed_lang": "en", "feed_start_date": "20260101",
            "feed_end_date": "20261231", "feed_version": "1.0",
            "feed_contact_email": "", "feed_contact_url": "",
        }],
    }


def _import_gtfs(source) -> TransitDataset:
    ds = TransitDataset(":memory:")
    ds.import_data(GtfsImporter(), source)
    return ds


def _gtfs_roundtrip(zip_path, tmp_path, name="reexported.zip"):
    """feed -> DB1 -> feed' -> DB2; returns (DB1, DB2, feed' path)."""
    first = _import_gtfs(zip_path)
    exported = tmp_path / name
    first.export_data(GtfsExporter(), exported)
    second = _import_gtfs(exported)
    return first, second, exported


def _read_csv(zip_path, member) -> List[Dict[str, str]]:
    with zipfile.ZipFile(zip_path) as zf:
        text = zf.read(member).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


class TestGtfsRoundTripIdentity:
    """export_G ∘ import_G ≅ id_G (formalization §1), checked via FeedDiffer."""

    def test_rich_feed_is_lossless(self, rich_gtfs_records, tmp_path):
        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))

        first, second, _ = _gtfs_roundtrip(zip_path, tmp_path)
        report = FeedDiffer(first, second).diff()

        details = {
            ed.entity_type: {"added": ed.added, "removed": ed.removed,
                             "modified": ed.modified}
            for ed in report.entity_diffs if ed.has_changes
        }
        assert not report.has_changes, f"round trip is lossy: {details}"

    def test_minimal_feed_is_lossless(self, minimal_gtfs_records, tmp_path):
        zip_path = tmp_path / "minimal.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))

        first, second, _ = _gtfs_roundtrip(zip_path, tmp_path)
        assert not FeedDiffer(first, second).diff().has_changes

    def test_double_roundtrip_is_stable(self, rich_gtfs_records, tmp_path):
        """The pipeline is idempotent: a second pass changes nothing more."""
        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))

        _, second, exported = _gtfs_roundtrip(zip_path, tmp_path)
        _, third, _ = _gtfs_roundtrip(exported, tmp_path, name="reexported2.zip")
        assert not FeedDiffer(second, third).diff().has_changes


class TestFieldPreservation:
    def test_new_fields_imported(self, rich_gtfs_records, tmp_path):
        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))
        ds = _import_gtfs(zip_path)

        sj = ds.db.get_one("service_journey", where={"id": "T1"})
        assert sj["headsign"] == "Downtown"
        assert sj["short_name"] == "101"
        assert sj["block_id"] == "B1"

        stop = ds.db.get_one("scheduled_stop_point", where={"id": "S1"})
        assert stop["code"] == "1001"
        assert stop["platform_code"] == "A"
        assert stop["zone_id"] == "Z1"
        assert stop["description"] == "Main platform"

        line = ds.db.get_one("line", where={"id": "R1"})
        assert line["text_color"] == "FFFFFF"
        assert line["description"] == "Main line"
        assert line["url"] == "https://example.com/r1"
        assert line["sort_order"] == 10

        pt = ds.db.get_one("passing_time",
                           where={"service_journey_id": "T1", "order": 1})
        assert pt["pickup_type"] == 0
        assert pt["drop_off_type"] == 1
        assert pt["timepoint"] == 1
        assert pt["stop_headsign"] == "Downtown via Main"

    def test_extended_route_type_reexports_exactly(self, rich_gtfs_records, tmp_path):
        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))
        _, _, exported = _gtfs_roundtrip(zip_path, tmp_path)

        routes = {r["route_id"]: r for r in _read_csv(exported, "routes.txt")}
        assert routes["R700"]["route_type"] == "700"
        assert routes["R1"]["route_type"] == "3"

    def test_unknown_columns_survive_via_extras(self, rich_gtfs_records, tmp_path):
        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))
        first, second, exported = _gtfs_roundtrip(zip_path, tmp_path)

        line = first.db.get_one("line", where={"id": "R1"})
        assert json.loads(line["extras"])["network_id"] == "N1"

        routes = {r["route_id"]: r for r in _read_csv(exported, "routes.txt")}
        assert routes["R1"]["network_id"] == "N1"
        assert routes["R700"]["network_id"] == ""  # empty on rows without it

        stops = {r["stop_id"]: r for r in _read_csv(exported, "stops.txt")}
        assert stops["S1"]["tts_stop_name"] == "Stop One"

        # and it survives into the second database identically
        line2 = second.db.get_one("line", where={"id": "R1"})
        assert line2["extras"] == line["extras"]

    def test_direction_id_roundtrips(self, rich_gtfs_records, tmp_path):
        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))
        _, second, exported = _gtfs_roundtrip(zip_path, tmp_path)

        trips = {t["trip_id"]: t for t in _read_csv(exported, "trips.txt")}
        assert trips["T1"]["direction_id"] == "0"
        assert trips["T2"]["direction_id"] == "1"

        jp = second.db.get_one("journey_pattern", where={"id": "JP_T2"})
        assert jp["direction"] == "inbound"


class TestNetexRoundTrip:
    def test_netex_roundtrip_is_idempotent(self, rich_gtfs_records, tmp_path):
        """DB1 -> NeTEx -> DB2 -> NeTEx -> DB3: DB2 ≅ DB3.

        The first NeTEx pass may normalize (GTFS-only entities drop out);
        idempotence asserts nothing changes after that normalization.
        """
        from pid_transit.adapters.netex_importer import NetexImporter
        from pid_transit.adapters.netex_exporter import NetexExporter

        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))
        first = _import_gtfs(zip_path)

        netex1 = tmp_path / "feed1.xml"
        first.export_data(NetexExporter(deduplicate_patterns=False), netex1)
        second = TransitDataset(":memory:")
        second.import_data(NetexImporter(), netex1)

        netex2 = tmp_path / "feed2.xml"
        second.export_data(NetexExporter(deduplicate_patterns=False), netex2)
        third = TransitDataset(":memory:")
        third.import_data(NetexImporter(), netex2)

        report = FeedDiffer(second, third).diff()
        details = {
            ed.entity_type: {"added": ed.added, "removed": ed.removed,
                             "modified": ed.modified}
            for ed in report.entity_diffs if ed.has_changes
        }
        assert not report.has_changes, f"NeTEx pipeline not idempotent: {details}"

        # core timetable content survives the first pass
        for table in ("service_journey", "passing_time"):
            assert first.db.count(table) == second.db.count(table), table


class TestNetexSaysWhatTheModelHolds:
    """The acceptance criterion of NETEX_EXPORT_AUDITORIA.md, as a test.

    The audit's finding was not that the exporter wrote a bad file -- the file
    passed the CEN XSD with zero errors, had no dangling reference and lost no
    timetable. It was that six fields left the document without appearing in any
    count, and that the worst of them, the timezone, silently moved all 461 359
    passing times of the STCP feed by an hour.

    So the criterion has two halves and both are here: a field the writer places
    comes back through a round trip that is given *no* fallback to lean on, and
    a field it cannot place is a number in the return value. A test that checked
    only the first half would let the next silent drop through.
    """

    @pytest.fixture
    def roundtripped(self, rich_gtfs_records, tmp_path):
        from pid_transit.adapters.netex_importer import NetexImporter
        from pid_transit.adapters.netex_exporter import NetexExporter

        # Not UTC. With UTC the assertion below would pass just as well against
        # an exporter that wrote no timezone at all, because UTC is exactly what
        # the importer falls back to -- which is how the defect survived.
        rich_gtfs_records["agency"][0]["agency_timezone"] = "Europe/Lisbon"
        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))
        source = _import_gtfs(zip_path)

        netex = tmp_path / "feed.xml"
        losses = source.export_data(
            NetexExporter(deduplicate_patterns=False), netex)
        back = TransitDataset(":memory:")
        # Deliberately no fallback_timezone: the document has to carry it.
        back.import_data(NetexImporter(), netex)
        return source, back, losses

    def test_the_timezone_survives_without_being_told_it(self, roundtripped):
        _, back, _ = roundtripped
        assert back.db.get_records("operator")[0]["timezone"] == "Europe/Lisbon"

    def test_the_operator_keeps_its_url_and_language(self, roundtripped):
        _, back, _ = roundtripped
        operator = back.db.get_records("operator")[0]
        assert operator["url"] == "https://example.com"
        assert operator["lang"] == "en"

    def test_the_line_keeps_its_colours_url_and_description(self, roundtripped):
        _, back, _ = roundtripped
        line = back.db.get_one("line", {"id": "R1"})
        assert line["color"] == "FF0000"
        assert line["text_color"] == "FFFFFF"
        assert line["url"] == "https://example.com/r1"
        assert line["description"] == "Main line"

    def test_the_stop_keeps_its_rider_facing_code(self, roundtripped):
        _, back, _ = roundtripped
        assert back.db.get_one("scheduled_stop_point", {"id": "S1"})["code"] == "1001"

    def test_the_feed_keeps_its_provenance(self, roundtripped):
        # The whole feed_info table used to disappear, and with it the publisher,
        # the validity window and the version -- who made the file and for when.
        # The EPIP DataSource holds a name, a description and an email only.
        # Without the publisher URL, which GTFS requires, the record cannot be
        # rebuilt, so under EPIP the fields it cannot hold are counted; the
        # CEN form keeps them all (test below).
        _, _, losses = roundtripped
        for field in ("publisher_url", "start_date", "end_date", "version"):
            assert losses[f"unsupported_field_feed_info_{field}"] == 1

    def test_the_cen_form_keeps_the_whole_provenance(self, roundtripped, tmp_path):
        from pid_transit.adapters.netex_export_profiles import ExportProfile
        from pid_transit.adapters.netex_exporter import NetexExporter
        from pid_transit.adapters.netex_importer import NetexImporter
        source, _, _ = roundtripped
        netex = tmp_path / "cen.xml"
        source.export_data(NetexExporter(profile=ExportProfile(),
                                         deduplicate_patterns=False), netex)
        back = TransitDataset(":memory:")
        back.import_data(NetexImporter(), netex)
        info = back.db.get_records("feed_info")
        assert info[0]["publisher_url"] == "https://example.com"
        assert (info[0]["start_date"], info[0]["end_date"]) == ("20260101", "20261231")
        assert info[0]["version"] == "1.0"

    def test_every_field_that_did_not_survive_is_counted(self, roundtripped):
        # The half that keeps the other half honest. Whatever the writer cannot
        # place has to appear in the return value; a field that is neither in
        # the document nor in the losses is the defect the audit found.
        source, back, losses = roundtripped
        for table, key in (("line", "id"), ("scheduled_stop_point", "id"),
                           ("service_journey", "id")):
            for row in source.db.get_records(table):
                after = back.db.get_one(table, {key: row[key]})
                if after is None:
                    continue
                for field, before in row.items():
                    if field == "extras" or before in (None, ""):
                        continue
                    if after.get(field) in (None, ""):
                        assert f"unsupported_field_{table}_{field}" in losses, (
                            f"{table}.{field} left the document uncounted")

    def test_entities_with_no_netex_home_are_counted_whole(self, roundtripped):
        # Fares and attributions have no element this writer emits at all.
        # That is a decision, not an accident, and it is a number.
        source, _, losses = roundtripped
        for table in ("fare_attribute", "fare_rule", "attribution"):
            expected = source.db.count(table)
            if expected:
                assert losses.get(f"unsupported_{table}") == expected, table

    def test_epip_counts_the_station_structure(self, roundtripped):
        # The EPIP StopPlace admits quays only, so stations and levels are
        # counted rather than written.
        source, _, losses = roundtripped
        for table in ("stop_area", "level"):
            if source.db.get_records(table):
                assert losses.get(f"unsupported_{table}") == len(
                    source.db.get_records(table)), table

    def test_the_station_structure_comes_back(self, roundtripped, tmp_path):
        # Under epip-stations, stations and levels have a home (StopPlace,
        # Level): not counted, and back with the same values.
        from pid_transit.adapters.netex_exporter import NetexExporter
        from pid_transit.adapters.netex_importer import NetexImporter
        source, _, _ = roundtripped
        netex = tmp_path / "stations.xml"
        losses = source.export_data(NetexExporter(profile="epip-stations",
                                                  deduplicate_patterns=False), netex)
        back = TransitDataset(":memory:")
        back.import_data(NetexImporter(), netex)
        for table in ("stop_area", "level"):
            assert f"unsupported_{table}" not in losses
            assert back.db.get_records(table) == source.db.get_records(table) or (
                [{k: v for k, v in r.items() if k != "extras"}
                 for r in back.db.get_records(table)]
                == [{k: v for k, v in r.items() if k != "extras"}
                    for r in source.db.get_records(table)]), table
        assert back.db.get_one("scheduled_stop_point", {"id": "S1"})[
            "stop_area_id"] == "STATION1"


class TestSchemaMigration:
    def test_31_database_gains_new_columns(self, tmp_path):
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE line (
                id TEXT PRIMARY KEY, operator_id TEXT, name TEXT NOT NULL,
                short_name TEXT, transport_mode TEXT NOT NULL, color TEXT
            )""")
        conn.execute("""
            CREATE TABLE service_journey (
                id TEXT PRIMARY KEY, line_id TEXT NOT NULL,
                journey_pattern_id TEXT, day_type_id TEXT NOT NULL,
                departure_time TEXT NOT NULL, wheelchair_accessible INTEGER,
                bikes_allowed INTEGER, shape_id TEXT
            )""")
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO _meta VALUES ('schema_version', '3.1.0-transmodel')")
        conn.execute(
            "INSERT INTO line (id, name, transport_mode) VALUES ('L1', 'Line', 'bus')")
        conn.commit()
        conn.close()

        db = TransmodelDatabase(db_path)

        conn = db.connect()
        version = conn.execute(
            "SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()[0]
        line_cols = {r[1] for r in conn.execute("PRAGMA table_info(line)")}
        sj_cols = {r[1] for r in conn.execute("PRAGMA table_info(service_journey)")}
        conn.close()

        assert version == SCHEMA_VERSION
        assert {"text_color", "description", "url", "sort_order",
                "extras"} <= line_cols
        assert {"headsign", "short_name", "block_id", "extras"} <= sj_cols

        # old data still readable through the new model
        line = db.get_one("line", where={"id": "L1"})
        assert line["name"] == "Line"
        assert line["extras"] is None

    def test_migration_is_idempotent(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        TransmodelDatabase(db_path)   # create at current version
        db = TransmodelDatabase(db_path)  # reopen: must be a clean no-op
        assert db.count("line") == 0


class TestIndexes:
    def test_fk_lookups_use_indexes(self, populated_db):
        conn = populated_db.connect()
        for sql, index in [
            ("SELECT * FROM service_journey WHERE line_id = 'L1'", "idx_sj_line"),
            ("SELECT * FROM service_journey WHERE day_type_id = 'D'", "idx_sj_day_type"),
            ("SELECT * FROM journey_pattern WHERE line_id = 'L1'", "idx_jp_line"),
            ("SELECT * FROM passing_time WHERE stop_point_id = 'S1'", "idx_pt_stop"),
        ]:
            plan = " ".join(
                row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}"))
            assert index in plan, f"expected {index} in plan: {plan}"

    def test_pk_prefix_covers_passing_time_by_journey(self, populated_db):
        conn = populated_db.connect()
        plan = " ".join(row[3] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM passing_time "
            "WHERE service_journey_id = 'T1'"))
        assert "SCAN passing_time" not in plan

    def test_bulk_session_drops_and_rebuilds_indexes(self, tmp_path):
        db = TransmodelDatabase(tmp_path / "bulk.db")

        def index_names(conn):
            return {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name LIKE 'idx_%'")}

        with db.bulk_session() as conn:
            assert not index_names(conn)  # dropped during the load

        conn = db.connect()
        try:
            names = index_names(conn)
        finally:
            conn.close()
        assert "idx_sj_line" in names  # rebuilt afterwards


class TestRoundtripCli:
    def test_cli_reports_lossless(self, rich_gtfs_records, tmp_path,
                                  monkeypatch, capsys):
        from pid_transit import cli

        zip_path = tmp_path / "rich.zip"
        zip_path.write_bytes(make_gtfs_zip(rich_gtfs_records))
        monkeypatch.setattr(sys, "argv", [
            "pid-transit", "roundtrip", str(zip_path), "--format", "gtfs"])

        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        assert "LOSSLESS" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Property-based fuzzing (optional: requires hypothesis)
# ---------------------------------------------------------------------------

try:
    from hypothesis import given, settings, HealthCheck
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover - environment without dev extras
    HAS_HYPOTHESIS = False

if HAS_HYPOTHESIS:
    _ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    _TEXT_ALPHABET = _ID_ALPHABET + "abcdefghijklmnopqrstuvwxyz "

    _names = (st.text(alphabet=_TEXT_ALPHABET, min_size=1, max_size=16)
              .map(str.strip).filter(bool))
    _opt_flag = st.sampled_from(["", "0", "1", "2"])


    def _fmt_time(seconds: int) -> str:
        return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


    @st.composite
    def _gtfs_feeds(draw):
        n_stops = draw(st.integers(min_value=2, max_value=4))
        stops = []
        for i in range(n_stops):
            stops.append({
                "stop_id": f"S{i}",
                "stop_name": draw(_names),
                "stop_lat": str(round(draw(st.floats(min_value=-89, max_value=89,
                                                     allow_nan=False)), 6)),
                "stop_lon": str(round(draw(st.floats(min_value=-179, max_value=179,
                                                     allow_nan=False)), 6)),
                "location_type": "0",
                "zone_id": draw(st.sampled_from(["", "Z1", "Z2"])),
                "stop_code": draw(st.sampled_from(["", "C1", "C77"])),
            })

        route_type = draw(st.sampled_from(["0", "1", "2", "3", "4", "5", "6", "7",
                                           "11", "12", "700", "900", "1300"]))
        routes = [{
            "route_id": "R1",
            "agency_id": "A1",
            "route_short_name": draw(st.sampled_from(["", "1", "X9"])),
            "route_long_name": draw(_names),
            "route_type": route_type,
            "route_color": draw(st.sampled_from(["", "FF0000", "00FF77"])),
        }]

        n_trips = draw(st.integers(min_value=1, max_value=3))
        trips, stop_times = [], []
        for t in range(n_trips):
            trip_id = f"T{t}"
            trips.append({
                "trip_id": trip_id,
                "route_id": "R1",
                "service_id": "SVC",
                "direction_id": draw(st.sampled_from(["", "0", "1"])),
                "trip_headsign": draw(st.one_of(st.just(""), _names)),
                "block_id": draw(st.sampled_from(["", "B1"])),
            })
            # strictly increasing times, may cross midnight (>= 24:00:00)
            time_s = draw(st.integers(min_value=0, max_value=26 * 3600))
            for seq in range(n_stops):
                arr = _fmt_time(time_s)
                time_s += draw(st.integers(min_value=0, max_value=300))
                dep = _fmt_time(time_s)
                stop_times.append({
                    "trip_id": trip_id,
                    "stop_id": f"S{seq}",
                    "stop_sequence": str(seq + 1),
                    "arrival_time": arr,
                    "departure_time": dep,
                    "pickup_type": draw(_opt_flag),
                    "drop_off_type": draw(_opt_flag),
                    "timepoint": draw(st.sampled_from(["", "0", "1"])),
                })
                time_s += draw(st.integers(min_value=1, max_value=1800))

        return {
            "agency": [{
                "agency_id": "A1",
                "agency_name": draw(_names),
                "agency_url": "https://example.com",
                "agency_timezone": "UTC",
            }],
            "stops": stops,
            "routes": routes,
            "calendar": [{
                "service_id": "SVC",
                "monday": draw(st.sampled_from(["0", "1"])),
                "tuesday": draw(st.sampled_from(["0", "1"])),
                "wednesday": "1", "thursday": "1", "friday": "1",
                "saturday": draw(st.sampled_from(["0", "1"])),
                "sunday": "0",
                "start_date": "20260101",
                "end_date": "20261231",
            }],
            "trips": trips,
            "stop_times": stop_times,
        }


    class TestPropertyBasedRoundTrip:
        @settings(max_examples=25, deadline=None,
                  suppress_health_check=[HealthCheck.too_slow])
        @given(records=_gtfs_feeds())
        def test_generated_feeds_roundtrip_losslessly(self, records, tmp_path_factory):
            tmp_path = tmp_path_factory.mktemp("hyp")
            zip_path = tmp_path / "gen.zip"
            zip_path.write_bytes(make_gtfs_zip(records))

            first, second, _ = _gtfs_roundtrip(zip_path, tmp_path)
            report = FeedDiffer(first, second).diff()

            details = {
                ed.entity_type: {"added": ed.added, "removed": ed.removed,
                                 "modified": ed.modified}
                for ed in report.entity_diffs if ed.has_changes
            }
            assert not report.has_changes, f"round trip is lossy: {details}"
