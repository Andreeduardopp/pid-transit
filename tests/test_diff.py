"""Tests for pid_transit.analytics.diff."""

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.core.dataset import TransitDataset
from pid_transit.core.schemas import TransportMode
from pid_transit.analytics.diff import FeedDiffer


def _make_dataset(records: dict) -> TransitDataset:
    ds = TransitDataset(":memory:")
    for table, rows in records.items():
        ds.db.upsert(table, rows)
    return ds


@pytest.fixture
def base_records():
    return {
        "operator": [{"id": "OP1", "name": "Agency", "timezone": "UTC"}],
        "line": [{"id": "L1", "operator_id": "OP1", "name": "Line 1",
                  "transport_mode": TransportMode.BUS}],
        "scheduled_stop_point": [
            {"id": "S1", "name": "Stop 1", "lat": 40.0, "lon": -74.0},
            {"id": "S2", "name": "Stop 2", "lat": 40.1, "lon": -74.1},
        ],
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


class TestFeedDiffer:
    def test_identical_feeds(self, base_records):
        base = _make_dataset(base_records)
        target = _make_dataset(base_records)
        report = FeedDiffer(base, target).diff()
        d = report.to_dict()
        assert d["entities"] == {}

    def test_added_stop(self, base_records):
        target_records = dict(base_records)
        target_records["scheduled_stop_point"] = base_records["scheduled_stop_point"] + [
            {"id": "S3", "name": "Stop 3", "lat": 40.2, "lon": -74.2},
        ]
        base = _make_dataset(base_records)
        target = _make_dataset(target_records)
        report = FeedDiffer(base, target).diff()
        d = report.to_dict()
        assert d["entities"]["scheduled_stop_point"]["added"] == 1

    def test_removed_operator(self, base_records):
        target_records = dict(base_records)
        target_records["operator"] = []
        target_records["line"] = []
        target_records["journey_pattern"] = []
        target_records["service_journey"] = []
        target_records["point_in_journey_pattern"] = []
        target_records["passing_time"] = []
        base = _make_dataset(base_records)
        target = _make_dataset(target_records)
        report = FeedDiffer(base, target).diff()
        d = report.to_dict()
        assert d["entities"]["operator"]["removed"] == 1

    def test_modified_stop(self, base_records):
        target_records = dict(base_records)
        target_records["scheduled_stop_point"] = [
            {"id": "S1", "name": "Renamed Stop", "lat": 40.0, "lon": -74.0},
            {"id": "S2", "name": "Stop 2", "lat": 40.1, "lon": -74.1},
        ]
        base = _make_dataset(base_records)
        target = _make_dataset(target_records)
        report = FeedDiffer(base, target).diff()
        d = report.to_dict()
        assert d["entities"]["scheduled_stop_point"]["modified"] == 1

    def test_to_markdown(self, base_records):
        target_records = dict(base_records)
        target_records["scheduled_stop_point"] = base_records["scheduled_stop_point"] + [
            {"id": "S3", "name": "Stop 3", "lat": 40.2, "lon": -74.2},
        ]
        base = _make_dataset(base_records)
        target = _make_dataset(target_records)
        report = FeedDiffer(base, target).diff()
        md = report.to_markdown()
        assert "Feed Comparison Report" in md
        assert "scheduled_stop_point" in md
