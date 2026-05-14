"""Tests for pid_transit.analytics.merge."""

import pytest

from pid_transit.core.dataset import TransitDataset
from pid_transit.core.schemas import TransportMode
from pid_transit.analytics.merge import FeedMerger


def _make_dataset(records: dict) -> TransitDataset:
    ds = TransitDataset(":memory:")
    for table, rows in records.items():
        ds.db.upsert(table, rows)
    return ds


@pytest.fixture
def base_records():
    return {
        "operator": [{"id": "OP1", "name": "Base Agency", "timezone": "UTC"}],
        "scheduled_stop_point": [
            {"id": "S1", "name": "Stop A", "lat": 40.0, "lon": -74.0},
        ],
        "line": [{"id": "L1", "operator_id": "OP1", "name": "Line 1",
                  "transport_mode": TransportMode.BUS}],
        "day_type": [{"id": "WD", "monday": True, "tuesday": True,
                      "wednesday": True, "thursday": True, "friday": True,
                      "saturday": False, "sunday": False,
                      "start_date": "20260101", "end_date": "20261231"}],
    }


@pytest.fixture
def source_records():
    return {
        "operator": [{"id": "OP1", "name": "Source Agency", "timezone": "UTC"}],
        "scheduled_stop_point": [
            {"id": "S1", "name": "Source Stop", "lat": 41.0, "lon": -73.0},
            {"id": "S2", "name": "Source Stop 2", "lat": 41.1, "lon": -73.1},
        ],
        "line": [{"id": "L1", "operator_id": "OP1", "name": "Source Line",
                  "transport_mode": TransportMode.TRAM}],
        "day_type": [{"id": "WD", "monday": True, "tuesday": True,
                      "wednesday": True, "thursday": True, "friday": True,
                      "saturday": False, "sunday": False,
                      "start_date": "20260101", "end_date": "20261231"}],
        "journey_pattern": [{"id": "JP1", "line_id": "L1", "direction": "outbound"}],
        "service_journey": [{"id": "T1", "line_id": "L1",
                             "journey_pattern_id": "JP1", "day_type_id": "WD",
                             "departure_time": "08:00:00"}],
        "point_in_journey_pattern": [
            {"journey_pattern_id": "JP1", "stop_point_id": "S1", "order": 0},
            {"journey_pattern_id": "JP1", "stop_point_id": "S2", "order": 1},
        ],
        "passing_time": [
            {"service_journey_id": "T1", "stop_point_id": "S1", "order": 0,
             "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            {"service_journey_id": "T1", "stop_point_id": "S2", "order": 1,
             "arrival_time": "08:10:00", "departure_time": "08:10:00"},
        ],
    }


class TestFeedMerger:
    def test_merge_namespaces_ids(self, base_records, source_records):
        target = _make_dataset(base_records)
        source = _make_dataset(source_records)

        merger = FeedMerger(target)
        stats = merger.merge(source, "src")

        assert target.db.count("operator") == 2
        assert target.db.get_one("operator", where={"id": "src:OP1"}) is not None
        assert target.db.get_one("operator", where={"id": "OP1"}) is not None

    def test_merge_remaps_fks(self, base_records, source_records):
        target = _make_dataset(base_records)
        source = _make_dataset(source_records)

        merger = FeedMerger(target)
        merger.merge(source, "src")

        line = target.db.get_one("line", where={"id": "src:L1"})
        assert line is not None
        assert line["operator_id"] == "src:OP1"

        sj = target.db.get_one("service_journey", where={"id": "src:T1"})
        assert sj["line_id"] == "src:L1"
        assert sj["journey_pattern_id"] == "src:JP1"
        assert sj["day_type_id"] == "src:WD"

    def test_merge_composite_tables(self, base_records, source_records):
        target = _make_dataset(base_records)
        source = _make_dataset(source_records)

        merger = FeedMerger(target)
        merger.merge(source, "src")

        pts = target.db.get_records("passing_time")
        src_pts = [p for p in pts if p["service_journey_id"] == "src:T1"]
        assert len(src_pts) == 2
        assert all(p["stop_point_id"].startswith("src:") for p in src_pts)

    def test_merge_multiple_sources(self, base_records, source_records):
        target = _make_dataset(base_records)
        source1 = _make_dataset(source_records)
        source2 = _make_dataset(source_records)

        merger = FeedMerger(target)
        merger.merge(source1, "feed_a")
        merger.merge(source2, "feed_b")

        assert target.db.count("operator") == 3

    def test_merge_stops_by_proximity(self):
        target_records = {
            "operator": [{"id": "OP1", "name": "Target", "timezone": "UTC"}],
            "scheduled_stop_point": [
                {"id": "T_S1", "name": "Target Stop", "lat": 40.0, "lon": -74.0},
            ],
        }
        source_records = {
            "operator": [{"id": "OP1", "name": "Source", "timezone": "UTC"}],
            "scheduled_stop_point": [
                {"id": "S1", "name": "Nearby Stop", "lat": 40.00001, "lon": -74.00001},
            ],
        }
        target = _make_dataset(target_records)
        source = _make_dataset(source_records)

        merger = FeedMerger(target)
        merger.merge(source, "src", merge_stops_threshold_m=50)

        assert target.db.count("scheduled_stop_point") == 1
        assert target.db.get_one("scheduled_stop_point", where={"id": "T_S1"}) is not None

    def test_custom_separator(self, base_records, source_records):
        target = _make_dataset(base_records)
        source = _make_dataset(source_records)

        merger = FeedMerger(target, namespace_separator="/")
        merger.merge(source, "src")

        assert target.db.get_one("operator", where={"id": "src/OP1"}) is not None
