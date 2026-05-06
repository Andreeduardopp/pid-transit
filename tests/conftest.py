"""Shared pytest fixtures for PID-GTFS."""

from pathlib import Path
from typing import Any, Dict, List

import pytest

from pid_transit.legacy import GtfsDatabase


@pytest.fixture
def db(tmp_path: Path) -> GtfsDatabase:
    """A fresh GtfsDatabase in a temp directory."""
    return GtfsDatabase(tmp_path / "test.db")


@pytest.fixture
def minimal_feed_records() -> Dict[str, List[Dict[str, Any]]]:
    """The smallest set of records that satisfies GTFS required tables."""
    return {
        "agency": [{
            "agency_id": "A1",
            "agency_name": "Test Agency",
            "agency_url": "https://example.com",
            "agency_timezone": "UTC",
        }],
        "stops": [
            {"stop_id": "S1", "stop_name": "Stop 1", "stop_lat": 40.0, "stop_lon": -74.0},
            {"stop_id": "S2", "stop_name": "Stop 2", "stop_lat": 40.1, "stop_lon": -74.1},
        ],
        "routes": [{
            "route_id": "R1",
            "agency_id": "A1",
            "route_short_name": "1",
            "route_long_name": "Route 1",
            "route_type": 3,
        }],
        "calendar": [{
            "service_id": "WEEKDAY",
            "monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1,
            "saturday": 0, "sunday": 0,
            "start_date": "20260101",
            "end_date": "20261231",
        }],
        "trips": [{
            "trip_id": "T1",
            "route_id": "R1",
            "service_id": "WEEKDAY",
        }],
        "stop_times": [
            {"trip_id": "T1", "stop_id": "S1", "stop_sequence": 1,
             "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            {"trip_id": "T1", "stop_id": "S2", "stop_sequence": 2,
             "arrival_time": "08:10:00", "departure_time": "08:10:00"},
        ],
    }


@pytest.fixture
def populated_db(db: GtfsDatabase, minimal_feed_records) -> GtfsDatabase:
    """A database seeded with a minimal valid feed."""
    # Insert in FK-safe order
    for table in ("agency", "stops", "routes", "calendar", "trips", "stop_times"):
        result = db.upsert(table, minimal_feed_records[table])
        assert result.failed == 0, f"seed failed for {table}: {result.errors}"
    return db
