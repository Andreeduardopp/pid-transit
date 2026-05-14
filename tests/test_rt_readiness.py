"""Tests for pid_transit.analytics.rt_readiness."""

import pytest

from pid_transit.core.dataset import TransitDataset
from pid_transit.core.schemas import TransportMode
from pid_transit.analytics.rt_readiness import check_rt_readiness


def _make_dataset(records: dict) -> TransitDataset:
    ds = TransitDataset(":memory:")
    for table, rows in records.items():
        ds.db.upsert(table, rows)
    return ds


@pytest.fixture
def clean_records():
    return {
        "operator": [{"id": "OP1", "name": "Agency", "timezone": "UTC"}],
        "scheduled_stop_point": [
            {"id": "S1", "name": "Stop 1", "lat": 40.0, "lon": -74.0},
            {"id": "S2", "name": "Stop 2", "lat": 40.1, "lon": -74.1},
        ],
        "line": [{"id": "L1", "operator_id": "OP1", "name": "Line 1",
                  "transport_mode": TransportMode.BUS}],
        "day_type": [{"id": "WD", "monday": True, "tuesday": True,
                      "wednesday": True, "thursday": True, "friday": True,
                      "saturday": False, "sunday": False,
                      "start_date": "20260101", "end_date": "20261231"}],
        "journey_pattern": [{"id": "JP1", "line_id": "L1"}],
        "service_journey": [{"id": "T1", "line_id": "L1",
                             "journey_pattern_id": "JP1", "day_type_id": "WD",
                             "departure_time": "08:00:00"}],
        "point_in_journey_pattern": [
            {"journey_pattern_id": "JP1", "stop_point_id": "S1", "order": 0},
            {"journey_pattern_id": "JP1", "stop_point_id": "S2", "order": 1},
        ],
    }


class TestRTReadiness:
    def test_clean_feed_is_ready(self, clean_records):
        ds = _make_dataset(clean_records)
        report = check_rt_readiness(ds)
        assert report.is_ready
        assert len(report.issues) == 0
        assert len(report.checks_passed) == 4

    def test_to_dict(self, clean_records):
        ds = _make_dataset(clean_records)
        report = check_rt_readiness(ds)
        d = report.to_dict()
        assert d["is_ready"] is True
        assert d["issue_count"] == 0

    def test_to_markdown(self, clean_records):
        ds = _make_dataset(clean_records)
        report = check_rt_readiness(ds)
        md = report.to_markdown()
        assert "READY" in md
        assert "Checks Passed" in md

    def test_detects_unsafe_id(self, clean_records):
        records = dict(clean_records)
        records["scheduled_stop_point"] = [
            {"id": "stop with spaces", "name": "Bad", "lat": 40.0, "lon": -74.0},
        ]
        records["point_in_journey_pattern"] = [
            {"journey_pattern_id": "JP1", "stop_point_id": "stop with spaces", "order": 0},
        ]
        ds = _make_dataset(records)
        report = check_rt_readiness(ds)
        assert not report.is_ready
        unsafe = [i for i in report.issues if i.category == "unsafe_id"]
        assert len(unsafe) >= 1

    def test_consistent_sequence_passes(self, clean_records):
        ds = _make_dataset(clean_records)
        report = check_rt_readiness(ds)
        assert report.is_ready
        assert "All journey patterns have consistent stop sequences" in report.checks_passed
