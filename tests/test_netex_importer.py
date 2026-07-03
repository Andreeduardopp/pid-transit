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


# Production-style NeTEx: ScheduledStopPoint, Quay and StopPlace are distinct
# id spaces linked by PassengerStopAssignment, coordinates live on the Quay,
# and the JourneyPattern has no RouteRef of its own.
PRODUCTION_NETEX = """<?xml version="1.0" encoding="UTF-8"?>
<PublicationDelivery xmlns="http://www.netex.org.uk/netex" version="1.1">
 <dataObjects>
  <CompositeFrame id="cf" version="1">
   <frames>
    <ResourceFrame id="rf" version="1">
     <organisations>
      <Operator id="op:1" version="1"><Name>Test Operator</Name></Operator>
     </organisations>
    </ResourceFrame>
    <SiteFrame id="sf" version="1">
     <stopPlaces>
      <StopPlace id="sp:1" version="1">
       <Name>Main Station</Name>
       <Centroid><Location><Longitude>8.50</Longitude><Latitude>47.40</Latitude></Location></Centroid>
       <quays>
        <Quay id="quay:1" version="1">
         <Name>Main Station Pl. 1</Name>
         <Centroid><Location><Longitude>8.51</Longitude><Latitude>47.41</Latitude></Location></Centroid>
        </Quay>
       </quays>
      </StopPlace>
      <StopPlace id="sp:2" version="1">
       <Name>Second Stop</Name>
       <Centroid><Location><Longitude>8.60</Longitude><Latitude>47.50</Latitude></Location></Centroid>
      </StopPlace>
     </stopPlaces>
    </SiteFrame>
    <ServiceFrame id="svf" version="1">
     <lines>
      <Line id="line:1" version="1">
       <Name>Line One</Name><TransportMode>bus</TransportMode>
       <OperatorRef ref="op:1"/>
      </Line>
     </lines>
     <scheduledStopPoints>
      <ScheduledStopPoint id="ssp:1" version="1"><Name>Main Station</Name></ScheduledStopPoint>
      <ScheduledStopPoint id="ssp:2" version="1"><Name>Second Stop</Name></ScheduledStopPoint>
      <ScheduledStopPoint id="ssp:3" version="1"><Name>Mystery Stop</Name></ScheduledStopPoint>
     </scheduledStopPoints>
     <stopAssignments>
      <PassengerStopAssignment id="psa:1" version="1" order="1">
       <ScheduledStopPointRef ref="ssp:1"/>
       <StopPlaceRef ref="sp:1"/>
       <QuayRef ref="quay:1"/>
      </PassengerStopAssignment>
      <PassengerStopAssignment id="psa:2" version="1" order="2">
       <ScheduledStopPointRef ref="ssp:2"/>
       <StopPlaceRef ref="sp:2"/>
      </PassengerStopAssignment>
     </stopAssignments>
     <journeyPatterns>
      <JourneyPattern id="jp:1" version="1">
       <pointsInSequence>
        <StopPointInJourneyPattern id="spijp:1" version="1" order="1">
         <ScheduledStopPointRef ref="ssp:1"/>
        </StopPointInJourneyPattern>
        <StopPointInJourneyPattern id="spijp:2" version="1" order="2">
         <ScheduledStopPointRef ref="ssp:2"/>
        </StopPointInJourneyPattern>
       </pointsInSequence>
      </JourneyPattern>
     </journeyPatterns>
    </ServiceFrame>
    <ServiceCalendarFrame id="scf" version="1">
     <dayTypes>
      <DayType id="dt:1" version="1">
       <properties><PropertyOfDay><DaysOfWeek>Monday Tuesday</DaysOfWeek></PropertyOfDay></properties>
      </DayType>
     </dayTypes>
    </ServiceCalendarFrame>
    <TimetableFrame id="tf" version="1">
     <vehicleJourneys>
      <ServiceJourney id="sj:1" version="1">
       <DepartureTime>08:00:00</DepartureTime>
       <dayTypes><DayTypeRef ref="dt:1"/></dayTypes>
       <JourneyPatternRef ref="jp:1"/>
       <LineRef ref="line:1"/>
       <passingTimes>
        <TimetabledPassingTime version="0">
         <StopPointInJourneyPatternRef ref="spijp:1"/>
         <DepartureTime>08:00:00</DepartureTime>
        </TimetabledPassingTime>
        <TimetabledPassingTime version="0">
         <StopPointInJourneyPatternRef ref="spijp:2"/>
         <ArrivalTime>08:10:00</ArrivalTime>
        </TimetabledPassingTime>
       </passingTimes>
      </ServiceJourney>
     </vehicleJourneys>
    </TimetableFrame>
   </frames>
  </CompositeFrame>
 </dataObjects>
</PublicationDelivery>
"""


class TestProductionStopResolution:
    @pytest.fixture()
    def imported(self, db, tmp_path):
        path = tmp_path / "prod.xml"
        path.write_text(PRODUCTION_NETEX, encoding="utf-8")
        stats = NetexImporter().import_to_db(db, path)
        return db, stats

    def test_stops_keyed_by_scheduled_stop_point_id(self, imported):
        db, stats = imported
        assert stats["scheduled_stop_point"] == 3
        ids = {s["id"] for s in db.get_records("scheduled_stop_point")}
        assert ids == {"ssp:1", "ssp:2", "ssp:3"}

    def test_coords_resolved_via_assignment_to_quay(self, imported):
        db, _ = imported
        s1 = db.get_one("scheduled_stop_point", where={"id": "ssp:1"})
        assert (s1["lat"], s1["lon"]) == (47.41, 8.51)  # Quay, not StopPlace centroid

    def test_coords_resolved_via_assignment_to_stop_place(self, imported):
        db, _ = imported
        s2 = db.get_one("scheduled_stop_point", where={"id": "ssp:2"})
        assert (s2["lat"], s2["lon"]) == (47.50, 8.60)

    def test_unresolvable_stop_kept_without_coords(self, imported):
        db, _ = imported
        s3 = db.get_one("scheduled_stop_point", where={"id": "ssp:3"})
        assert s3 is not None
        assert s3["name"] == "Mystery Stop"
        assert s3["lat"] is None and s3["lon"] is None

    def test_passing_times_resolve_to_stops(self, imported):
        db, stats = imported
        assert stats["passing_time"] == 2
        pts = db.query("passing_time", where={"service_journey_id": "sj:1"}, order_by="order")
        assert [pt["stop_point_id"] for pt in pts] == ["ssp:1", "ssp:2"]

    def test_journey_pattern_line_resolved_from_service_journey(self, imported):
        db, _ = imported
        jp = db.get_one("journey_pattern", where={"id": "jp:1"})
        assert jp["line_id"] == "line:1"


LEGACY_STOPPLACE_NETEX = """<?xml version="1.0" encoding="UTF-8"?>
<PublicationDelivery xmlns="http://www.netex.org.uk/netex" version="1.1">
 <dataObjects>
  <SiteFrame id="sf" version="1">
   <stopPlaces>
    <StopPlace id="sp:good" version="1">
     <Name>Good</Name>
     <Centroid><Location><Longitude>8.5</Longitude><Latitude>47.4</Latitude></Location></Centroid>
    </StopPlace>
    <StopPlace id="sp:bad" version="1">
     <Name>Bad Coords</Name>
     <Centroid><Location><Longitude>abc</Longitude><Latitude>xyz</Latitude></Location></Centroid>
    </StopPlace>
   </stopPlaces>
  </SiteFrame>
 </dataObjects>
</PublicationDelivery>
"""


def _wrap_frames(*frames: str) -> str:
    inner = "\n".join(frames)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<PublicationDelivery xmlns="http://www.netex.org.uk/netex" version="1.1">\n'
        f' <dataObjects><CompositeFrame id="cf" version="1"><frames>\n{inner}\n'
        ' </frames></CompositeFrame></dataObjects>\n'
        '</PublicationDelivery>\n'
    )


def _split_production_netex() -> dict:
    """PRODUCTION_NETEX split across three files like real datasets ship it.

    The timetable member is named to sort first, proving import is
    order-independent."""
    import re
    frame = {}
    for name in ("ResourceFrame", "SiteFrame", "ServiceFrame",
                 "ServiceCalendarFrame", "TimetableFrame"):
        frame[name] = re.search(
            rf"<{name}\b.*?</{name}>", PRODUCTION_NETEX, re.S
        ).group(0)
    return {
        "0_timetable.xml": _wrap_frames(frame["TimetableFrame"]),
        "1_sites.xml": _wrap_frames(frame["SiteFrame"]),
        "2_services.xml": _wrap_frames(
            frame["ResourceFrame"], frame["ServiceFrame"], frame["ServiceCalendarFrame"]
        ),
    }


class TestMultiFileSources:
    EXPECTED = {
        "operator": 1, "scheduled_stop_point": 3, "day_type": 1, "line": 1,
        "journey_pattern": 1, "point_in_journey_pattern": 2,
        "service_journey": 1, "passing_time": 2,
    }

    def _assert_full_import(self, db, stats):
        for table, count in self.EXPECTED.items():
            assert stats.get(table, 0) == count, f"{table}: {stats}"
        pts = db.query("passing_time", where={"service_journey_id": "sj:1"}, order_by="order")
        assert [pt["stop_point_id"] for pt in pts] == ["ssp:1", "ssp:2"]

    def test_zip_with_timetable_sorting_first(self, db, tmp_path):
        import zipfile
        zip_path = tmp_path / "feed_netex.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in _split_production_netex().items():
                zf.writestr(name, content)
        stats = NetexImporter().import_to_db(db, zip_path)
        self._assert_full_import(db, stats)

    def test_directory(self, db, tmp_path):
        d = tmp_path / "netex_dir"
        d.mkdir()
        for name, content in _split_production_netex().items():
            (d / name).write_text(content, encoding="utf-8")
        stats = NetexImporter().import_to_db(db, d)
        self._assert_full_import(db, stats)

    def test_list_of_files(self, db, tmp_path):
        paths = []
        for name, content in _split_production_netex().items():
            p = tmp_path / name
            p.write_text(content, encoding="utf-8")
            paths.append(p)
        stats = NetexImporter().import_to_db(db, paths)
        self._assert_full_import(db, stats)

    def test_matches_single_file_import(self, db, tmp_path):
        single = tmp_path / "single.xml"
        single.write_text(PRODUCTION_NETEX, encoding="utf-8")
        single_stats = NetexImporter().import_to_db(db, single)

        d = tmp_path / "netex_dir"
        d.mkdir()
        for name, content in _split_production_netex().items():
            (d / name).write_text(content, encoding="utf-8")
        db2 = TransmodelDatabase(":memory:")
        multi_stats = NetexImporter().import_to_db(db2, d)
        assert multi_stats == single_stats

    def test_empty_directory_raises(self, db, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(ImportError, match="No .xml files"):
            NetexImporter().import_to_db(db, d)


# Swiss-SKI-style: no journey patterns; the stop sequence lives directly on
# the ServiceJourney as calls/Call elements referencing ScheduledStopPoints.
CALLS_NETEX = """<?xml version="1.0" encoding="UTF-8"?>
<PublicationDelivery xmlns="http://www.netex.org.uk/netex" version="1.1">
 <dataObjects>
  <CompositeFrame id="cf" version="1">
   <frames>
    <SiteFrame id="sf" version="1">
     <stopPlaces>
      <StopPlace id="sp:1" version="1"><Name>Alpha</Name>
       <Centroid><Location><Longitude>8.1</Longitude><Latitude>47.1</Latitude></Location></Centroid>
      </StopPlace>
      <StopPlace id="sp:2" version="1"><Name>Beta</Name>
       <Centroid><Location><Longitude>8.2</Longitude><Latitude>47.2</Latitude></Location></Centroid>
      </StopPlace>
     </stopPlaces>
    </SiteFrame>
    <ServiceFrame id="svf" version="1">
     <lines>
      <Line id="line:1" version="1"><Name>L1</Name><TransportMode>rail</TransportMode></Line>
     </lines>
     <scheduledStopPoints>
      <ScheduledStopPoint id="ssp:1" version="1"><Name>Alpha</Name></ScheduledStopPoint>
      <ScheduledStopPoint id="ssp:2" version="1"><Name>Beta</Name></ScheduledStopPoint>
     </scheduledStopPoints>
     <stopAssignments>
      <PassengerStopAssignment id="psa:1" version="1" order="1">
       <ScheduledStopPointRef ref="ssp:1"/><StopPlaceRef ref="sp:1"/>
      </PassengerStopAssignment>
      <PassengerStopAssignment id="psa:2" version="1" order="2">
       <ScheduledStopPointRef ref="ssp:2"/><StopPlaceRef ref="sp:2"/>
      </PassengerStopAssignment>
     </stopAssignments>
    </ServiceFrame>
    <TimetableFrame id="tf" version="1">
     <vehicleJourneys>
      <ServiceJourney id="sj:1" version="1">
       <DepartureTime>23:50:00</DepartureTime>
       <LineRef ref="line:1"/>
       <calls>
        <Call id="c:1" version="1" order="1">
         <ScheduledStopPointRef ref="ssp:1"/>
         <Departure><Time>23:50:00</Time></Departure>
        </Call>
        <Call id="c:2" version="1" order="2">
         <ScheduledStopPointRef ref="ssp:2"/>
         <Arrival><Time>00:15:00</Time><DayOffset>1</DayOffset></Arrival>
        </Call>
        <Call id="c:3" version="1" order="3">
         <ScheduledStopPointRef ref="ssp:unknown"/>
         <Arrival><Time>00:30:00</Time><DayOffset>1</DayOffset></Arrival>
        </Call>
       </calls>
      </ServiceJourney>
     </vehicleJourneys>
    </TimetableFrame>
   </frames>
  </CompositeFrame>
 </dataObjects>
</PublicationDelivery>
"""


RICH_GTFS_RECORDS = {
    "agency": [{"agency_id": "A1", "agency_name": "Test", "agency_url": "https://x.com",
                "agency_timezone": "UTC"}],
    "stops": [
        {"stop_id": "S1", "stop_name": "One", "stop_lat": "40.0", "stop_lon": "-74.0",
         "location_type": "0", "wheelchair_boarding": "1"},
        {"stop_id": "S2", "stop_name": "Two", "stop_lat": "40.1", "stop_lon": "-74.1",
         "location_type": "0", "wheelchair_boarding": "2"},
    ],
    "routes": [{"route_id": "R1", "agency_id": "A1", "route_short_name": "1",
                "route_long_name": "Route", "route_type": "3"}],
    "calendar": [{"service_id": "WD", "monday": "1", "tuesday": "1", "wednesday": "1",
                  "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
                  "start_date": "20260101", "end_date": "20261231"}],
    "calendar_dates": [
        {"service_id": "WD", "date": "20260704", "exception_type": "2"},
        {"service_id": "WD", "date": "20260705", "exception_type": "1"},
    ],
    "trips": [{"trip_id": "T1", "route_id": "R1", "service_id": "WD",
               "shape_id": "SH1", "wheelchair_accessible": "1"}],
    "stop_times": [
        {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1",
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"trip_id": "T1", "stop_id": "S2", "stop_sequence": "2",
         "arrival_time": "08:10:00", "departure_time": "08:10:00"},
    ],
    "shapes": [
        {"shape_id": "SH1", "shape_pt_lat": "40.0", "shape_pt_lon": "-74.0", "shape_pt_sequence": "1"},
        {"shape_id": "SH1", "shape_pt_lat": "40.05", "shape_pt_lon": "-74.05", "shape_pt_sequence": "2"},
        {"shape_id": "SH1", "shape_pt_lat": "40.1", "shape_pt_lon": "-74.1", "shape_pt_sequence": "3"},
    ],
    "frequencies": [{"trip_id": "T1", "start_time": "06:00:00", "end_time": "10:00:00",
                     "headway_secs": "600", "exact_times": "1"}],
    "transfers": [{"from_stop_id": "S1", "to_stop_id": "S2", "transfer_type": "2",
                   "min_transfer_time": "120"}],
}


class TestCoverageSymmetryRoundTrip:
    """GTFS -> NeTEx export -> NeTEx import: previously-lost data must survive."""

    @pytest.fixture()
    def round_tripped(self, db, tmp_path):
        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(RICH_GTFS_RECORDS))
        GtfsImporter().import_to_db(db, zip_path)
        netex_path = tmp_path / "out.xml"
        NetexExporter(deduplicate_patterns=False).export_from_db(db, netex_path)
        db2 = TransmodelDatabase(":memory:")
        stats = NetexImporter().import_to_db(db2, netex_path)
        return db, db2, stats

    def test_calendar_validity_survives(self, round_tripped):
        _, db2, _ = round_tripped
        dt = db2.get_one("day_type", where={"id": "WD"})
        assert dt["start_date"] == "20260101"
        assert dt["end_date"] == "20261231"
        assert dt["monday"] == 1 and dt["saturday"] == 0

    def test_calendar_exceptions_survive(self, round_tripped):
        _, db2, _ = round_tripped
        excs = db2.query("operating_day_exception", where={"day_type_id": "WD"})
        by_date = {e["date"]: e["is_addition"] for e in excs}
        assert by_date == {"20260704": 0, "20260705": 1}

    def test_shapes_survive(self, round_tripped):
        db, db2, _ = round_tripped
        assert db2.count("shape_point") == 3
        points = db2.query("shape_point", order_by="sequence")
        assert [p["lat"] for p in points] == [40.0, 40.05, 40.1]
        sj = db2.get_one("service_journey", where={"id": "T1"})
        assert sj["shape_id"] is not None
        # journey's shape resolves to actual points
        assert db2.count("shape_point", where={"shape_id": sj["shape_id"]}) == 3

    def test_transfers_survive(self, round_tripped):
        _, db2, _ = round_tripped
        tr = db2.get_one("transfer", where={"from_stop_id": "S1"})
        assert tr["to_stop_id"] == "S2"
        assert tr["transfer_type"] == 2
        assert tr["min_transfer_time"] == 120

    def test_frequencies_survive(self, round_tripped):
        _, db2, _ = round_tripped
        fr = db2.get_one("frequency", where={"service_journey_id": "T1"})
        assert fr["start_time"] == "06:00:00"
        assert fr["end_time"] == "10:00:00"
        assert fr["headway_secs"] == 600
        assert fr["exact_times"] == 1

    def test_accessibility_survives(self, round_tripped):
        _, db2, _ = round_tripped
        assert db2.get_one("scheduled_stop_point", where={"id": "S1"})["wheelchair_boarding"] == 1
        assert db2.get_one("scheduled_stop_point", where={"id": "S2"})["wheelchair_boarding"] == 2
        assert db2.get_one("service_journey", where={"id": "T1"})["wheelchair_accessible"] == 1


# Swiss-style AvailabilityCondition calendar: FromDate + ValidDayBits bitmask
# referenced from the journey's validityConditions.
AVAILABILITY_NETEX = """<?xml version="1.0" encoding="UTF-8"?>
<PublicationDelivery xmlns="http://www.netex.org.uk/netex" version="1.1">
 <dataObjects>
  <CompositeFrame id="cf" version="1">
   <frames>
    <SiteFrame id="sf" version="1">
     <stopPlaces>
      <StopPlace id="sp:1" version="1"><Name>Alpha</Name>
       <Centroid><Location><Longitude>8.1</Longitude><Latitude>47.1</Latitude></Location></Centroid>
      </StopPlace>
     </stopPlaces>
    </SiteFrame>
    <ServiceCalendarFrame id="scf" version="1">
     <validityConditions>
      <AvailabilityCondition id="ac:1" version="1">
       <FromDate>2026-01-05T00:00:00</FromDate>
       <ToDate>2026-01-11T23:59:59</ToDate>
       <ValidDayBits>1010100</ValidDayBits>
      </AvailabilityCondition>
     </validityConditions>
    </ServiceCalendarFrame>
    <ServiceFrame id="svf" version="1">
     <lines><Line id="line:1" version="1"><Name>L1</Name><TransportMode>bus</TransportMode></Line></lines>
    </ServiceFrame>
    <TimetableFrame id="tf" version="1">
     <vehicleJourneys>
      <ServiceJourney id="sj:1" version="1">
       <validityConditions>
        <AvailabilityConditionRef ref="ac:1" versionRef="any"/>
       </validityConditions>
       <DepartureTime>08:00:00</DepartureTime>
       <LineRef ref="line:1"/>
       <calls>
        <Call id="c:1" version="1" order="1">
         <ScheduledStopPointRef ref="sp:1"/>
         <Departure><Time>08:00:00</Time></Departure>
        </Call>
       </calls>
      </ServiceJourney>
     </vehicleJourneys>
    </TimetableFrame>
   </frames>
  </CompositeFrame>
 </dataObjects>
</PublicationDelivery>
"""


class TestAvailabilityConditionCalendar:
    def test_condition_becomes_day_type_with_exceptions(self, db, tmp_path):
        path = tmp_path / "avail.xml"
        path.write_text(AVAILABILITY_NETEX, encoding="utf-8")
        stats = NetexImporter().import_to_db(db, path)

        sj = db.get_one("service_journey", where={"id": "sj:1"})
        assert sj["day_type_id"] == "ac:1"

        dt = db.get_one("day_type", where={"id": "ac:1"})
        assert dt["start_date"] == "20260105"
        assert dt["end_date"] == "20260111"
        assert dt["monday"] == 0  # bitmask encoded as exceptions, not weekdays

        excs = db.query("operating_day_exception", where={"day_type_id": "ac:1"})
        dates = sorted(e["date"] for e in excs)
        # bits 1010100 from 2026-01-05: Mon, Wed, Fri
        assert dates == ["20260105", "20260107", "20260109"]
        assert all(e["is_addition"] for e in excs)
    def test_calls_become_passing_times(self, db, tmp_path):
        path = tmp_path / "calls.xml"
        path.write_text(CALLS_NETEX, encoding="utf-8")
        stats = NetexImporter().import_to_db(db, path)

        assert stats["service_journey"] == 1
        # third call references an unknown stop and is dropped
        assert stats["passing_time"] == 2
        pts = db.query("passing_time", where={"service_journey_id": "sj:1"}, order_by="order")
        assert pts[0]["stop_point_id"] == "ssp:1"
        assert pts[0]["departure_time"] == "23:50:00"
        # DayOffset folded into the GTFS-style >24h time
        assert pts[1]["arrival_time"] == "24:15:00"

    def test_journey_without_known_line_skipped(self, db, tmp_path):
        content = CALLS_NETEX.replace('LineRef ref="line:1"', 'LineRef ref="line:ghost"')
        path = tmp_path / "calls.xml"
        path.write_text(content, encoding="utf-8")
        stats = NetexImporter().import_to_db(db, path)
        assert stats.get("service_journey", 0) == 0
        assert stats.get("passing_time", 0) == 0


class TestProgressAndCancellation:
    def test_netex_progress_callback(self, db, tmp_path):
        path = tmp_path / "prod.xml"
        path.write_text(PRODUCTION_NETEX, encoding="utf-8")

        events = []

        def on_progress(entity_type, count, *, file_index=None, total_files=None):
            events.append((entity_type, count, file_index, total_files))

        stats = NetexImporter(on_progress=on_progress).import_to_db(db, path)

        assert events, "no progress events fired"
        by_entity = {}
        for entity_type, count, file_index, total_files in events:
            assert entity_type in stats
            # cumulative counts never decrease
            assert count >= by_entity.get(entity_type, 0)
            by_entity[entity_type] = count
            assert total_files == 1
        # final reported count matches the returned stats
        for entity_type, count in by_entity.items():
            assert stats[entity_type] == count

    def test_netex_cancel_before_start(self, db, tmp_path):
        import threading
        from pid_transit.core.exceptions import ImportCancelledError

        path = tmp_path / "prod.xml"
        path.write_text(PRODUCTION_NETEX, encoding="utf-8")
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ImportCancelledError):
            NetexImporter(cancel_event=cancel).import_to_db(db, path)
        assert db.count("scheduled_stop_point") == 0

    def test_netex_cancel_mid_import(self, tmp_path):
        import threading
        from pid_transit.core.exceptions import ImportCancelledError

        path = tmp_path / "prod.xml"
        path.write_text(PRODUCTION_NETEX, encoding="utf-8")
        cancel = threading.Event()

        def cancel_after_first_event(entity_type, count, **kw):
            cancel.set()

        db = TransmodelDatabase(tmp_path / "cancelled.db")
        importer = NetexImporter(on_progress=cancel_after_first_event, cancel_event=cancel)
        with pytest.raises(ImportCancelledError):
            importer.import_to_db(db, path)

    def test_gtfs_progress_and_cancel(self, minimal_gtfs_records, tmp_path):
        import threading
        from pid_transit.core.exceptions import ImportCancelledError

        zip_path = tmp_path / "feed.zip"
        zip_path.write_bytes(make_gtfs_zip(minimal_gtfs_records))

        events = []
        db = TransmodelDatabase(":memory:")
        stats = GtfsImporter(
            on_progress=lambda et, c, **kw: events.append((et, c))
        ).import_to_db(db, zip_path)
        reported = {et for et, _ in events}
        assert "operator" in reported and "passing_time" in reported
        for et, c in events:
            assert c <= stats[et]

        cancel = threading.Event()
        cancel.set()
        db2 = TransmodelDatabase(":memory:")
        with pytest.raises(ImportCancelledError):
            GtfsImporter(cancel_event=cancel).import_to_db(db2, zip_path)


class TestLegacyStopPlaceFallback:
    def test_bad_coords_kept_not_dropped(self, db, tmp_path):
        path = tmp_path / "legacy.xml"
        path.write_text(LEGACY_STOPPLACE_NETEX, encoding="utf-8")
        stats = NetexImporter().import_to_db(db, path)
        assert stats["scheduled_stop_point"] == 2
        bad = db.get_one("scheduled_stop_point", where={"id": "sp:bad"})
        assert bad["name"] == "Bad Coords"
        assert bad["lat"] is None and bad["lon"] is None
        good = db.get_one("scheduled_stop_point", where={"id": "sp:good"})
        assert (good["lat"], good["lon"]) == (47.4, 8.5)
