"""Tests for the NeTEx exporter's schema-order and ServiceLink geometry.

Geometry is the delicate part: a GTFS shape is one polyline for a whole
pattern, while NeTEx carries it on ServiceLinks — one per hop between
consecutive stops — so the exporter has to cut it, and the cut has to agree
with the pattern's own stop sequence.
"""

import logging
import xml.etree.ElementTree as ET

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.adapters.netex_exporter import (
    NetexExporter,
    _order_frame_children,
    _split_shape_by_stops,
)

NS = {"n": "http://www.netex.org.uk/netex", "g": "http://www.opengis.net/gml/3.2"}


def _seeded_db(stops, patterns, points, journeys, shape_points):
    """A database holding just enough to exercise the ServiceFrame writer."""
    db = TransmodelDatabase(":memory:")
    db.upsert("operator", [{"id": "OP1", "name": "Agency", "timezone": "UTC"}])
    db.upsert("line", [{"id": "L1", "operator_id": "OP1", "name": "Line One",
                        "transport_mode": "bus"}])
    db.upsert("day_type", [{"id": "WEEKDAY", "monday": True,
                            "start_date": "20260101", "end_date": "20261231"}])
    db.upsert("scheduled_stop_point", stops)
    db.upsert("journey_pattern", patterns)
    db.upsert("point_in_journey_pattern", points)
    db.upsert("service_journey", journeys)
    if shape_points:
        db.upsert("shape_point", shape_points)
    return db


def _export_root(db, tmp_path, name="feed.xml"):
    out = tmp_path / name
    NetexExporter(deduplicate_patterns=False).export_from_db(db, out)
    return ET.parse(out).getroot()


def _links_of(root, jp_id):
    """The ServiceLink ids a JourneyPattern references, in order."""
    for jp in root.iter(f"{{{NS['n']}}}JourneyPattern"):
        if jp.attrib.get("id") != jp_id:
            continue
        return [r.attrib["ref"]
                for r in jp.findall("n:linksInSequence/n:ServiceLinkInJourneyPattern"
                                    "/n:ServiceLinkRef", NS)]
    return []


def _link_endpoints(root):
    """ServiceLink id -> (FromPointRef, ToPointRef)."""
    out = {}
    for link in root.iter(f"{{{NS['n']}}}ServiceLink"):
        frm = link.find("n:FromPointRef", NS)
        to = link.find("n:ToPointRef", NS)
        out[link.attrib["id"]] = (frm.attrib["ref"], to.attrib["ref"])
    return out


# A straight 9-point polyline; stops sit on points 0, 2, 4, 6 and 8.
STRAIGHT_SHAPE = [
    {"shape_id": "SH1", "lat": 0.0, "lon": float(i), "sequence": i}
    for i in range(9)
]
FIVE_STOPS = [
    {"id": f"S{i}", "name": f"Stop {i}", "lat": 0.0, "lon": float(i * 2)}
    for i in range(5)
]


class TestSharedShapeCutting:
    """A shape used by patterns with different stop sequences.

    Regression: links were cut once per shape, using whichever pattern reached
    it first, and every other pattern on that shape then referenced the whole
    list — emitting more links than it had hops, with endpoints naming stops it
    does not serve.
    """

    @pytest.fixture
    def two_patterns(self):
        # JPA calls at every stop; JPB is a short-turn skipping S1 and S3.
        patterns = [{"id": "JPA", "line_id": "L1", "direction": "outbound"},
                    {"id": "JPB", "line_id": "L1", "direction": "outbound"}]
        points = [
            {"journey_pattern_id": "JPA", "stop_point_id": f"S{i}", "order": i}
            for i in range(5)
        ] + [
            {"journey_pattern_id": "JPB", "stop_point_id": s, "order": i}
            for i, s in enumerate(("S0", "S2", "S4"))
        ]
        journeys = [
            {"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
             "day_type_id": "WEEKDAY", "departure_time": "08:00:00", "shape_id": "SH1"},
            {"id": "TB", "line_id": "L1", "journey_pattern_id": "JPB",
             "day_type_id": "WEEKDAY", "departure_time": "09:00:00", "shape_id": "SH1"},
        ]
        return _seeded_db(FIVE_STOPS, patterns, points, journeys, STRAIGHT_SHAPE)

    def test_each_pattern_gets_one_link_per_hop(self, two_patterns, tmp_path):
        root = _export_root(two_patterns, tmp_path)
        assert len(_links_of(root, "JPA")) == 4   # 5 stops -> 4 hops
        assert len(_links_of(root, "JPB")) == 2   # 3 stops -> 2 hops

    def test_patterns_do_not_share_a_cut(self, two_patterns, tmp_path):
        root = _export_root(two_patterns, tmp_path)
        assert not set(_links_of(root, "JPA")) & set(_links_of(root, "JPB"))

    def test_link_endpoints_match_the_pattern_stops(self, two_patterns, tmp_path):
        root = _export_root(two_patterns, tmp_path)
        endpoints = _link_endpoints(root)
        for jp_id, expected in (("JPA", ["S0", "S1", "S2", "S3", "S4"]),
                                ("JPB", ["S0", "S2", "S4"])):
            hops = [endpoints[lid] for lid in _links_of(root, jp_id)]
            assert [h[0] for h in hops] == expected[:-1]
            assert [h[1] for h in hops] == expected[1:]

    def test_first_cut_keeps_the_shape_id(self, two_patterns, tmp_path):
        # So the ordinary one-cut-per-shape feed round-trips to the id it came
        # from; only the extra cut is suffixed.
        root = _export_root(two_patterns, tmp_path)
        ids = _links_of(root, "JPA") + _links_of(root, "JPB")
        assert sum(i.startswith("SH1:link:") for i in ids) == 4
        assert sum(i.startswith("SH1:v2:link:") for i in ids) == 2

    def test_identical_stop_sequences_still_share_one_cut(self, tmp_path):
        patterns = [{"id": "JPA", "line_id": "L1", "direction": "outbound"},
                    {"id": "JPB", "line_id": "L1", "direction": "outbound"}]
        points = [
            {"journey_pattern_id": jp, "stop_point_id": f"S{i}", "order": i}
            for jp in ("JPA", "JPB") for i in range(5)
        ]
        journeys = [
            {"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
             "day_type_id": "WEEKDAY", "departure_time": "08:00:00", "shape_id": "SH1"},
            {"id": "TB", "line_id": "L1", "journey_pattern_id": "JPB",
             "day_type_id": "WEEKDAY", "departure_time": "09:00:00", "shape_id": "SH1"},
        ]
        db = _seeded_db(FIVE_STOPS, patterns, points, journeys, STRAIGHT_SHAPE)
        root = _export_root(db, tmp_path)
        assert _links_of(root, "JPA") == _links_of(root, "JPB")
        assert len(list(root.iter(f"{{{NS['n']}}}ServiceLink"))) == 4


class TestCoarseShapes:
    """A shape with fewer points than the pattern has stops cannot be cut."""

    @pytest.fixture
    def coarse(self):
        patterns = [{"id": "JPA", "line_id": "L1", "direction": "outbound"}]
        points = [{"journey_pattern_id": "JPA", "stop_point_id": f"S{i}", "order": i}
                  for i in range(5)]
        journeys = [{"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
                     "day_type_id": "WEEKDAY", "departure_time": "08:00:00",
                     "shape_id": "SH1"}]
        shape = [{"shape_id": "SH1", "lat": 0.0, "lon": float(i), "sequence": i}
                 for i in range(3)]
        return _seeded_db(FIVE_STOPS, patterns, points, journeys, shape)

    def test_geometry_is_dropped_but_the_pattern_survives(self, coarse, tmp_path):
        root = _export_root(coarse, tmp_path)
        assert not list(root.iter(f"{{{NS['n']}}}ServiceLink"))
        assert _links_of(root, "JPA") == []
        assert [jp.attrib["id"] for jp in root.iter(f"{{{NS['n']}}}JourneyPattern")] == ["JPA"]

    def test_the_drop_is_logged(self, coarse, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="pid_transit.adapters.netex_exporter"):
            _export_root(coarse, tmp_path)
        assert any("too few points" in r.getMessage() for r in caplog.records)


class TestSplitShapeByStops:
    def test_split_is_lossless(self):
        stops = [{"stop_point_id": f"S{i}"} for i in range(5)]
        coords = {f"S{i}": (0.0, float(i * 2)) for i in range(5)}
        segments = _split_shape_by_stops(STRAIGHT_SHAPE, stops, coords)
        # Concatenate, dropping each shared junction, and the original is back.
        rebuilt = list(segments[0][2])
        for _f, _t, pts in segments[1:]:
            rebuilt.extend(pts[1:] if pts[0] is rebuilt[-1] else pts)
        assert [p["lon"] for p in rebuilt] == [float(i) for i in range(9)]

    def test_every_hop_gets_at_least_two_points(self):
        # Nine stops on a nine-point shape: every cut is forced apart.
        stops = [{"stop_point_id": f"S{i}"} for i in range(9)]
        coords = {f"S{i}": (0.0, 0.0) for i in range(9)}   # all snap to index 0
        segments = _split_shape_by_stops(STRAIGHT_SHAPE, stops, coords)
        assert len(segments) == 8
        assert all(len(pts) >= 2 for _f, _t, pts in segments)

    def test_search_is_bounded_to_feasible_indices(self):
        # Every intermediate stop sits on the far end of the polyline. A global
        # argmin would put all of them at index 8; the bounded search has to
        # leave room for the hops that follow, so the cuts stay increasing.
        stops = [{"stop_point_id": f"S{i}"} for i in range(4)]
        coords = {f"S{i}": (0.0, 8.0) for i in range(4)}
        segments = _split_shape_by_stops(STRAIGHT_SHAPE, stops, coords)
        assert len(segments) == 3
        assert all(len(pts) >= 2 for _f, _t, pts in segments)

    def test_too_few_points_returns_empty(self):
        stops = [{"stop_point_id": f"S{i}"} for i in range(5)]
        short = STRAIGHT_SHAPE[:3]
        assert _split_shape_by_stops(short, stops, {}) == []

    def test_single_stop_returns_empty(self):
        assert _split_shape_by_stops(STRAIGHT_SHAPE, [{"stop_point_id": "S0"}], {}) == []


class TestSchemaOrder:
    def test_unknown_child_names_itself(self):
        frame = ET.Element("ServiceFrame")
        ET.SubElement(frame, "lines")
        ET.SubElement(frame, "destinationDisplays")
        with pytest.raises(ValueError, match="destinationDisplays.*ServiceFrame"):
            _order_frame_children(frame, ("Name", "lines"))

    def test_children_are_sorted_into_schema_order(self):
        frame = ET.Element("ServiceFrame")
        for tag in ("journeyPatterns", "lines", "connections"):
            ET.SubElement(frame, tag)
        _order_frame_children(frame, ("lines", "connections", "journeyPatterns"))
        assert [el.tag for el in frame] == ["lines", "connections", "journeyPatterns"]

    def test_service_frame_children_are_in_schema_order(self, tmp_path):
        patterns = [{"id": "JPA", "line_id": "L1", "direction": "outbound"}]
        points = [{"journey_pattern_id": "JPA", "stop_point_id": f"S{i}", "order": i}
                  for i in range(5)]
        journeys = [{"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
                     "day_type_id": "WEEKDAY", "departure_time": "08:00:00",
                     "shape_id": "SH1"}]
        db = _seeded_db(FIVE_STOPS, patterns, points, journeys, STRAIGHT_SHAPE)
        root = _export_root(db, tmp_path)
        frame = next(root.iter(f"{{{NS['n']}}}ServiceFrame"))
        tags = [el.tag.rpartition("}")[2] for el in frame]
        assert tags == sorted(tags, key=[
            "ValidBetween", "TypeOfFrameRef", "Name", "lines",
            "scheduledStopPoints", "serviceLinks", "connections",
            "stopAssignments", "journeyPatterns",
        ].index)

    def test_passing_times_precede_frequency_groups(self, tmp_path):
        patterns = [{"id": "JPA", "line_id": "L1", "direction": "outbound"}]
        points = [{"journey_pattern_id": "JPA", "stop_point_id": f"S{i}", "order": i}
                  for i in range(2)]
        journeys = [{"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
                     "day_type_id": "WEEKDAY", "departure_time": "08:00:00"}]
        db = _seeded_db(FIVE_STOPS[:2], patterns, points, journeys, [])
        db.upsert("passing_time", [
            {"service_journey_id": "TA", "stop_point_id": f"S{i}", "order": i,
             "arrival_time": f"08:0{i}:00", "departure_time": f"08:0{i}:00"}
            for i in range(2)
        ])
        db.upsert("frequency", [{
            "service_journey_id": "TA", "start_time": "08:00:00",
            "end_time": "10:00:00", "headway_secs": 600,
        }])
        root = _export_root(db, tmp_path)
        journey = next(root.iter(f"{{{NS['n']}}}TemplateServiceJourney"))
        tags = [el.tag.rpartition("}")[2] for el in journey]
        assert tags.index("passingTimes") < tags.index("frequencyGroups")


class TestPassingTimeAnchoring:
    """A TimetabledPassingTime names no stop of its own.

    It says only "the k-th point of this journey's pattern", so the writer's
    positional match is the only thing binding a time to a place. When the two
    sequences disagree, writing the ref anyway produced a schema-valid document
    stating a timetable the feed does not contain — and returned ``{}``.
    """

    PATTERN = [{"id": "JPA", "line_id": "L1", "direction": "outbound"}]

    def _db(self, journey_stops):
        points = [{"journey_pattern_id": "JPA", "stop_point_id": f"S{i}",
                   "order": i} for i in range(3)]
        journeys = [{"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
                     "day_type_id": "WEEKDAY", "departure_time": "08:00:00"}]
        db = _seeded_db(FIVE_STOPS[:3], self.PATTERN, points, journeys, [])
        db.upsert("passing_time", [
            {"service_journey_id": "TA", "stop_point_id": stop, "order": i,
             "arrival_time": f"08:0{i}:00", "departure_time": f"08:0{i}:00"}
            for i, stop in enumerate(journey_stops)
        ])
        return db

    @staticmethod
    def _export(db, tmp_path, dedup=False):
        out = tmp_path / "feed.xml"
        losses = NetexExporter(deduplicate_patterns=dedup).export_from_db(db, out)
        return ET.parse(out).getroot(), losses

    def _refs(self, root):
        return [el.get("ref") for el
                in root.iter(f"{{{NS['n']}}}StopPointInJourneyPatternRef")]

    def test_a_journey_that_follows_its_pattern_is_untouched(self, tmp_path):
        root, losses = self._export(self._db(["S0", "S1", "S2"]), tmp_path)
        assert self._refs(root) == ["JPA_stop_0", "JPA_stop_1", "JPA_stop_2"]
        assert losses == {}

    @pytest.mark.parametrize("dedup", [False, True])
    def test_a_time_at_the_wrong_stop_is_dropped_and_counted(self, tmp_path, dedup):
        # The journey runs the pattern backwards: only the middle position
        # names the same stop in both sequences.
        root, losses = self._export(self._db(["S2", "S1", "S0"]), tmp_path, dedup)
        assert self._refs(root) == ["JPA_stop_1"]
        assert losses["dropped_passing_time_stop_mismatch"] == 2

    def test_the_count_is_not_confused_with_an_overlong_journey(self, tmp_path):
        # Pattern S0,S1,S2 against a journey S0,S2,S2,S1: position 1 names the
        # wrong stop, position 3 is past the end of the pattern. Two different
        # questions -- fix the feed, or choose another pattern -- so two keys.
        root, losses = self._export(self._db(["S0", "S2", "S2", "S1"]), tmp_path)
        assert self._refs(root) == ["JPA_stop_0", "JPA_stop_2"]
        assert losses["dropped_passing_time_stop_mismatch"] == 1
        assert losses["dropped_passing_time_no_pattern_point"] == 1


class TestLossesAreOneDocument:
    def test_a_reused_exporter_does_not_add_up_its_runs(self, tmp_path):
        points = [{"journey_pattern_id": "JPA", "stop_point_id": f"S{i}",
                   "order": i} for i in range(2)]
        journeys = [{"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
                     "day_type_id": "WEEKDAY", "departure_time": "08:00:00"}]
        db = _seeded_db(FIVE_STOPS[:2],
                        [{"id": "JPA", "line_id": "L1", "direction": "outbound"}],
                        points, journeys, [])
        exporter = NetexExporter(profile="pt", deduplicate_patterns=False)
        first = exporter.export_from_db(db, tmp_path / "one.xml")
        second = exporter.export_from_db(db, tmp_path / "two.xml")
        assert first, "the PT profile has losses to report on this feed"
        assert second == first


class TestStopTypeFollowsTheMode:
    """A StopPlace is typed by the lines that call there, not assumed to be a bus.

    `StopPlaceType` was the constant "onstreetBus", so a metro network shipped
    a document whose Line said `metro` and whose every StopPlace said on-street
    bus stop. That is the false statement `vehicle_mode` already refuses to
    make, on the entity next door.
    """

    MODES = [("metro", "metroStation"), ("tram", "onstreetTram"),
             ("rail", "railStation"), ("ferry", "ferryStop"),
             ("funicular", "liftStation"), ("bus", "onstreetBus"),
             ("monorail", "other")]

    def _db(self, mode):
        db = _seeded_db(FIVE_STOPS[:2],
                        [{"id": "JPA", "line_id": "L1", "direction": "outbound"}],
                        [{"journey_pattern_id": "JPA", "stop_point_id": f"S{i}",
                          "order": i} for i in range(2)],
                        [{"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
                          "day_type_id": "WEEKDAY", "departure_time": "08:00:00"}],
                        [])
        db.upsert("line", [{"id": "L1", "operator_id": "OP1", "name": "Line One",
                            "transport_mode": mode}])
        return db

    @pytest.mark.parametrize("mode,stop_type", MODES)
    def test_the_stop_type_is_the_mode_that_serves_it(self, tmp_path, mode, stop_type):
        root = _export_root(self._db(mode), tmp_path)
        types = {el.text for el in root.iter(f"{{{NS['n']}}}StopPlaceType")}
        assert types == {stop_type}

    def test_a_stop_no_line_calls_at_takes_the_feed_s_own_mode(self, tmp_path):
        db = self._db("metro")
        db.upsert("scheduled_stop_point", [{"id": "S9", "name": "Orphan",
                                            "lat": 40.9, "lon": -74.9}])
        root = _export_root(db, tmp_path)
        types = {el.text for el in root.iter(f"{{{NS['n']}}}StopPlaceType")}
        assert types == {"metroStation"}, "one-mode feed has an honest answer"

    def test_an_untypable_stop_is_counted_once_not_once_per_element(self, tmp_path):
        # The PT profile locates its stop points, so it states the type twice
        # per stop -- StopPlaceType and StopType. One stop it cannot type is
        # one loss.
        out = tmp_path / "pt.xml"
        losses = NetexExporter(profile="pt", deduplicate_patterns=False
                               ).export_from_db(self._db("metro"), out)
        assert losses["unsupported_stop_place_type"] == 2, "two stops, two losses"


class TestDegenerateJourneyPattern:
    """CEN sequences two points in a JourneyPattern or none (minOccurs="2")."""

    def _db(self):
        db = _seeded_db(FIVE_STOPS[:1],
                        [{"id": "JPA", "line_id": "L1", "direction": "outbound"}],
                        [{"journey_pattern_id": "JPA", "stop_point_id": "S0",
                          "order": 0}],
                        [{"id": "TA", "line_id": "L1", "journey_pattern_id": "JPA",
                          "day_type_id": "WEEKDAY", "departure_time": "08:00:00"}],
                        [])
        db.upsert("passing_time", [{"service_journey_id": "TA",
                                    "stop_point_id": "S0", "order": 0,
                                    "departure_time": "08:00:00"}])
        return db

    def test_a_one_point_pattern_sequences_nothing_and_says_so(self, tmp_path):
        out = tmp_path / "feed.xml"
        losses = NetexExporter(deduplicate_patterns=False).export_from_db(
            self._db(), out)
        root = ET.parse(out).getroot()
        assert not list(root.iter(f"{{{NS['n']}}}pointsInSequence")), (
            "a single StopPointInJourneyPattern makes the document invalid"
        )
        assert losses["dropped_point_in_journey_pattern_single"] == 1

    def test_its_journey_references_no_point_that_was_not_written(self, tmp_path):
        out = tmp_path / "feed.xml"
        losses = NetexExporter(deduplicate_patterns=False).export_from_db(
            self._db(), out)
        root = ET.parse(out).getroot()
        assert not list(root.iter(f"{{{NS['n']}}}StopPointInJourneyPatternRef"))
        assert losses["dropped_passing_time_no_pattern_point"] == 1

    def test_a_one_call_journey_is_left_out_of_the_pt_document(self, tmp_path):
        # Same CEN rule on the calls form: `calls` sequences two Calls or none.
        out = tmp_path / "pt.xml"
        losses = NetexExporter(profile="pt", deduplicate_patterns=False
                               ).export_from_db(self._db(), out)
        root = ET.parse(out).getroot()
        assert not list(root.iter(f"{{{NS['n']}}}calls"))
        assert losses["dropped_call_single_journey"] == 1
