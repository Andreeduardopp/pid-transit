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
            "Name", "lines", "scheduledStopPoints", "serviceLinks", "connections",
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
