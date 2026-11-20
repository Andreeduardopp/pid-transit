"""Stations, entrances, generic nodes, levels and pathways through NeTEx.

GTFS states a station tree by parent ids (stops.txt location_type 1-3,
levels.txt, pathways.txt); NeTEx by containment: a StopPlace holding its
levels, entrances, Quays, AccessSpaces and SitePathLinks. Until 2026-11 the
exporter wrote one StopPlace per platform and counted the rest as losses, so a
railway feed (Euskotren: 131 stations, 157 entrances, 297 nodes, 4 levels, 745
pathways) came back with its platforms only.

The criterion is the round trip: GTFS -> model -> NeTEx -> model -> GTFS gives
back every station record field by field, and an export of a feed with no
stations is the document it always was.
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pid_transit.core.dataset import TransitDataset
from pid_transit.adapters.gtfs_importer import GtfsImporter
from pid_transit.adapters.gtfs_exporter import GtfsExporter
from pid_transit.adapters.netex_exporter import NetexExporter
from pid_transit.adapters.netex_importer import NetexImporter
from tests.conftest import make_gtfs_zip
from tests.test_roundtrip import _read_csv

NS = {"n": "http://www.netex.org.uk/netex"}

_STOP_COLS = ("stop_id", "stop_name", "stop_lat", "stop_lon", "location_type",
              "parent_station", "level_id", "wheelchair_boarding",
              "platform_code", "stop_code")
_PATHWAY_COLS = ("pathway_id", "from_stop_id", "to_stop_id", "pathway_mode",
                 "is_bidirectional", "length", "traversal_time", "stair_count",
                 "max_slope", "min_width", "signposted_as",
                 "reversed_signposted_as")


def _stop(stop_id, name, loc, parent="", level="", wheelchair="", lat="43.0",
          lon="-2.0", platform="", code=""):
    return dict(zip(_STOP_COLS, (stop_id, name, lat, lon, loc, parent, level,
                                 wheelchair, platform, code)))


def _pathway(pid, a, b, mode, bidi="1", **fields):
    row = dict.fromkeys(_PATHWAY_COLS, "")
    row.update(pathway_id=pid, from_stop_id=a, to_stop_id=b,
               pathway_mode=str(mode), is_bidirectional=bidi)
    row.update({k: str(v) for k, v in fields.items()})
    return row


def _records():
    """Two stations sharing two levels (one at a fractional index), every
    pathway mode, one-way and two-way, signed stair counts, every optional
    pathway field, and all four wheelchair values; plus a plain stop with no
    station, which must stay a StopPlace of its own."""
    return {
        "agency": [{"agency_id": "A1", "agency_name": "Rail",
                    "agency_url": "https://example.com",
                    "agency_timezone": "Europe/Madrid"}],
        "levels": [
            {"level_id": "L0", "level_index": "0", "level_name": "Street"},
            {"level_id": "LM", "level_index": "-1.5", "level_name": "Mezzanine"},
        ],
        "stops": [
            _stop("ST1", "North", "1", level="L0", wheelchair="1"),
            _stop("ST2", "South", "1", level="L0", wheelchair="0", lat="43.1"),
            _stop("P1", "North 1", "0", "ST1", "LM", "1", platform="1", code="N1"),
            _stop("P2", "North 2", "0", "ST1", "L0", "0", platform="2"),
            _stop("P3", "South 1", "0", "ST2", "LM", "2", lat="43.1"),
            _stop("E1", "North gate", "2", "ST1", "L0", "1"),
            _stop("E2", "South gate", "2", "ST2", "L0", "", lat="43.1"),
            _stop("N1", "North hall", "3", "ST1", "LM", "0"),
            _stop("N2", "South hall", "3", "ST2", "LM", "2", lat="43.1"),
            _stop("B1", "Bus stop", "0", wheelchair="1", lat="43.2"),
        ],
        "pathways": [
            _pathway("W1", "E1", "N1", 1, length="12.5", traversal_time="30",
                     signposted_as="Platforms", reversed_signposted_as="Exit"),
            _pathway("W2", "N1", "P1", 2, stair_count="-20", min_width="1.2"),
            _pathway("W3", "N1", "P2", 3, max_slope="0.08"),
            _pathway("W4", "N1", "P2", 4, bidi="0", stair_count="18"),
            _pathway("W5", "E2", "N2", 5),
            _pathway("W6", "N2", "P3", 6, bidi="0"),
            _pathway("W7", "P3", "N2", 7, bidi="0"),
            # Joins two stations: belongs to neither StopPlace.
            _pathway("X1", "E1", "E2", 1, length="300"),
        ],
        "routes": [{"route_id": "R1", "agency_id": "A1", "route_short_name": "1",
                    "route_long_name": "Line 1", "route_type": "2"}],
        "calendar": [{"service_id": "WK", "monday": "1", "tuesday": "1",
                      "wednesday": "1", "thursday": "1", "friday": "1",
                      "saturday": "0", "sunday": "0",
                      "start_date": "20260101", "end_date": "20261231"}],
        "trips": [{"trip_id": "T1", "route_id": "R1", "service_id": "WK"}],
        "stop_times": [
            {"trip_id": "T1", "stop_id": s, "stop_sequence": str(i),
             "arrival_time": t, "departure_time": t}
            for i, (s, t) in enumerate((("P1", "08:00:00"), ("P3", "08:10:00"),
                                        ("B1", "08:20:00")), start=1)
        ],
    }


@pytest.fixture
def feed(tmp_path):
    path = tmp_path / "stations.zip"
    path.write_bytes(make_gtfs_zip(_records()))
    return path


def _netex_round_trip(feed, tmp_path, profile="epip"):
    source = TransitDataset(":memory:")
    source.import_data(GtfsImporter(), feed)
    netex = tmp_path / "stations.xml"
    losses = source.export_data(NetexExporter(profile=profile), netex)
    back = TransitDataset(":memory:")
    stats = back.import_data(NetexImporter(), netex)
    out = tmp_path / "back.zip"
    back.export_data(GtfsExporter(), out)
    return netex, losses, stats, out


def _rows(path, name, key, cols):
    return {r[key]: tuple(r.get(c, "") for c in cols)
            for r in _read_csv(path, name)}


def _norm(rows):
    """Numbers compare as numbers ('0' == '0.0'), as the station check does."""
    def n(v):
        try:
            return repr(float(v)) if v != "" else ""
        except ValueError:
            return v
    return {k: tuple(n(v) for v in vals) for k, vals in rows.items()}


class TestRoundTrip:
    def test_every_stop_record_comes_back(self, feed, tmp_path):
        _, _, _, out = _netex_round_trip(feed, tmp_path)
        cols = [c for c in _STOP_COLS if c != "stop_id"]
        assert _norm(_rows(out, "stops.txt", "stop_id", cols)) == \
            _norm(_rows(feed, "stops.txt", "stop_id", cols))

    def test_levels_come_back_once_each(self, feed, tmp_path):
        _, _, _, out = _netex_round_trip(feed, tmp_path)
        cols = ("level_index", "level_name")
        assert _norm(_rows(out, "levels.txt", "level_id", cols)) == \
            _norm(_rows(feed, "levels.txt", "level_id", cols))

    def test_every_pathway_field_comes_back(self, feed, tmp_path):
        _, _, _, out = _netex_round_trip(feed, tmp_path)
        cols = _PATHWAY_COLS[1:]
        assert _norm(_rows(out, "pathways.txt", "pathway_id", cols)) == \
            _norm(_rows(feed, "pathways.txt", "pathway_id", cols))

    def test_nothing_of_the_structure_is_counted_lost(self, feed, tmp_path):
        _, losses, stats, _ = _netex_round_trip(feed, tmp_path)
        for key in ("unsupported_stop_area", "unsupported_level",
                    "unsupported_pathway", "dropped_level_ref",
                    "unsupported_field_scheduled_stop_point_stop_area_id",
                    "unsupported_field_scheduled_stop_point_level_id",
                    "unsupported_field_scheduled_stop_point_platform_code"):
            assert key not in losses, key
        assert "skipped_pathway" not in stats
        assert stats["stop_area"] == 6
        assert stats["level"] == 2
        assert stats["pathway"] == 8


class TestDocumentShape:
    @pytest.fixture
    def root(self, feed, tmp_path):
        netex, *_ = _netex_round_trip(feed, tmp_path)
        return ET.parse(netex).getroot()

    def test_a_station_holds_its_parts(self, root):
        st = root.find(".//n:StopPlace[@id='ST1']", NS)
        children = [el.tag.split("}")[1] for el in st]
        # CEN StopPlace sequence: levels, entrances before StopPlaceType;
        # quays, accessSpaces, pathLinks after it.
        assert children == ["keyList", "Name", "Centroid",
                            "AccessibilityAssessment", "levels", "entrances",
                            "StopPlaceType", "quays", "accessSpaces", "pathLinks"]
        assert {q.get("id") for q in st.findall("n:quays/n:Quay", NS)} == \
            {"P1:Quay", "P2:Quay"}
        assert st.find("n:StopPlaceType", NS).text == "railStation"

    def test_a_shared_level_is_copied_per_station_with_unique_ids(self, root):
        ids = [lv.get("id") for lv in root.iter(f"{{{NS['n']}}}Level")]
        assert len(ids) == len(set(ids)) == 4

    def test_a_stop_with_no_station_is_unchanged(self, root):
        sp = root.find(".//n:StopPlace[@id='B1']", NS)
        assert [q.get("id") for q in sp.findall("n:quays/n:Quay", NS)] == ["B1:Quay"]

    def test_a_pathway_between_stations_sits_in_the_frame(self, root):
        frame_links = root.findall(".//n:SiteFrame/n:pathLinks/n:PathLink", NS)
        assert [pl.get("id") for pl in frame_links] == ["X1"]


class TestProfiles:
    def test_the_portuguese_profile_still_counts_the_structure(self, feed, tmp_path):
        netex, losses, _, _ = _netex_round_trip(feed, tmp_path, profile="pt")
        assert losses["unsupported_stop_area"] == 6
        assert losses["unsupported_level"] == 2
        assert losses["unsupported_pathway"] == 8
        root = ET.parse(netex).getroot()
        assert root.find(".//n:Level", NS) is None
        assert root.find(".//n:StopPlace[@id='ST1']", NS) is None

    def test_unplaceable_records_are_counted(self, tmp_path):
        records = _records()
        # A boarding area, an entrance with no station, and a pathway to it.
        records["stops"] += [_stop("BA1", "Door 1", "4", "P1"),
                             _stop("EX", "Lost gate", "2")]
        records["pathways"].append(_pathway("Z1", "EX", "E1", 1))
        feed = tmp_path / "more.zip"
        feed.write_bytes(make_gtfs_zip(records))
        _, losses, _, _ = _netex_round_trip(feed, tmp_path)
        assert losses["unsupported_stop_area"] == 2
        assert losses["unsupported_pathway"] == 1


class TestThirdPartyNetex:
    """A file this library did not write: no keyList, levels named once."""

    DOC = """<?xml version="1.0" encoding="UTF-8"?>
<PublicationDelivery xmlns="http://www.netex.org.uk/netex" version="1.1">
 <dataObjects><CompositeFrame id="C" version="1"><frames>
  <SiteFrame id="S" version="1"><stopPlaces>
   <StopPlace id="X:SP:1" version="1">
    <Name>Central</Name>
    <Centroid><Location><Longitude>-2</Longitude><Latitude>43</Latitude></Location></Centroid>
    <levels><Level id="X:L:1" version="1"><Name>Street</Name>
      <RelativeLevelOrder>0</RelativeLevelOrder></Level></levels>
    <entrances><StopPlaceEntrance id="X:E:1" version="1"><Name>Main</Name>
      <LevelRef ref="X:L:1" version="1"/></StopPlaceEntrance></entrances>
    <StopPlaceType>railStation</StopPlaceType>
    <quays><Quay id="X:Q:1" version="1"><Name>Platform 1</Name>
      <Centroid><Location><Longitude>-2</Longitude><Latitude>43</Latitude></Location></Centroid>
      <AccessibilityAssessment id="X:Q:1:a" version="1">
       <MobilityImpairedAccess>unknown</MobilityImpairedAccess></AccessibilityAssessment>
      <LevelRef ref="X:L:1" version="1"/><PublicCode>1</PublicCode></Quay></quays>
    <pathLinks><SitePathLink id="X:PL:1" version="1">
      <From><PlaceRef ref="X:E:1" version="1"/></From>
      <To><PlaceRef ref="X:Q:1" version="1"/></To>
      <AllowedUse>oneWay</AllowedUse><AccessFeatureType>escalator</AccessFeatureType>
     </SitePathLink></pathLinks>
   </StopPlace></stopPlaces></SiteFrame>
  <ServiceFrame id="SF" version="1">
   <scheduledStopPoints><ScheduledStopPoint id="X:SSP:1" version="1"><Name>Platform 1</Name>
   </ScheduledStopPoint></scheduledStopPoints>
   <stopAssignments><PassengerStopAssignment id="X:PSA:1" version="1" order="1">
    <ScheduledStopPointRef ref="X:SSP:1" version="1"/><QuayRef ref="X:Q:1" version="1"/>
   </PassengerStopAssignment></stopAssignments>
  </ServiceFrame>
 </frames></CompositeFrame></dataObjects>
</PublicationDelivery>"""

    def test_the_structure_is_read_by_containment(self, tmp_path):
        path = tmp_path / "third.xml"
        path.write_text(self.DOC, encoding="utf-8")
        ds = TransitDataset(":memory:")
        ds.import_data(NetexImporter(), path)
        ssp = ds.db.get_one("scheduled_stop_point", {"id": "X:SSP:1"})
        assert (ssp["stop_area_id"], ssp["level_id"], ssp["platform_code"],
                ssp["wheelchair_boarding"]) == ("X:SP:1", "X:L:1", "1", 0)
        areas = {a["id"]: a for a in ds.db.get_records("stop_area")}
        assert areas["X:SP:1"]["location_type"] == 1
        assert (areas["X:E:1"]["location_type"], areas["X:E:1"]["parent_id"],
                areas["X:E:1"]["level_id"]) == (2, "X:SP:1", "X:L:1")
        (pw,) = ds.db.get_records("pathway")
        assert (pw["from_stop_id"], pw["to_stop_id"], pw["pathway_mode"],
                pw["is_bidirectional"]) == ("X:E:1", "X:SSP:1", 4, 0)


class TestNoStations:
    def test_a_one_stop_stop_place_does_not_become_a_station(self, tmp_path):
        """Our own export of a plain stop names its StopPlace after the stop;
        reading it back must not invent a station."""
        records = _records()
        records["stops"] = [r for r in records["stops"] if r["stop_id"] == "B1"]
        records["levels"] = []
        records["pathways"] = []
        records["stop_times"] = [r for r in records["stop_times"] if r["stop_id"] == "B1"]
        feed = tmp_path / "plain.zip"
        feed.write_bytes(make_gtfs_zip({k: v for k, v in records.items() if v}))
        _, _, stats, _ = _netex_round_trip(feed, tmp_path)
        assert "stop_area" not in stats


@pytest.mark.skipif(not os.environ.get("PID_NETEX_XSD"),
                    reason="set PID_NETEX_XSD to NeTEx_publication.xsd to run")
def test_the_document_is_valid_cen_netex(feed, tmp_path):
    from lxml import etree
    netex, *_ = _netex_round_trip(feed, tmp_path)
    schema = etree.XMLSchema(etree.parse(os.environ["PID_NETEX_XSD"]))
    assert schema.validate(etree.parse(str(netex))), schema.error_log


class TestMigration:
    def test_a_platform_level_moves_out_of_extras(self, tmp_path):
        """A 3.3 database kept a platform's level only in extras."""
        import sqlite3
        from pid_transit.core.database import TransmodelDatabase
        path = tmp_path / "old.db"
        TransmodelDatabase(path).close()
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE scheduled_stop_point DROP COLUMN level_id")
        conn.execute(
            "INSERT INTO scheduled_stop_point (id, name, lat, lon, extras) VALUES "
            "('P1', 'P', 43, -2, '{\"level_id\": \"L0\", \"tts_stop_name\": \"Pe\"}'),"
            "('P2', 'Q', 43, -2, '{\"level_id\": \"L1\"}')")
        conn.execute("UPDATE _meta SET value = '3.3.0-transmodel' "
                     "WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        db = TransmodelDatabase(path)
        rows = {r["id"]: r for r in db.get_records("scheduled_stop_point")}
        assert (rows["P1"]["level_id"], rows["P1"]["extras"]) == \
            ("L0", '{"tts_stop_name": "Pe"}')
        assert (rows["P2"]["level_id"], rows["P2"]["extras"]) == ("L1", None)
        db.close()
