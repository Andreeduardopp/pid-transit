"""Shared pytest fixtures for PID-Transit."""

import io
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.core.schemas import TransportMode, DirectionType


@pytest.fixture
def db() -> TransmodelDatabase:
    """A fresh in-memory TransmodelDatabase."""
    return TransmodelDatabase(":memory:")


@pytest.fixture
def sample_operator() -> Dict[str, Any]:
    return {
        "id": "OP1",
        "name": "Test Agency",
        "url": "https://example.com",
        "timezone": "UTC",
        "lang": "en",
        "phone": "555-0100",
    }


@pytest.fixture
def sample_stops() -> List[Dict[str, Any]]:
    return [
        {"id": "S1", "name": "Stop One", "lat": 40.0, "lon": -74.0},
        {"id": "S2", "name": "Stop Two", "lat": 40.1, "lon": -74.1},
    ]


@pytest.fixture
def sample_line() -> Dict[str, Any]:
    return {
        "id": "L1",
        "operator_id": "OP1",
        "name": "Line One",
        "short_name": "1",
        "transport_mode": TransportMode.BUS,
        "color": "FF0000",
    }


@pytest.fixture
def sample_day_type() -> Dict[str, Any]:
    return {
        "id": "WEEKDAY",
        "monday": True,
        "tuesday": True,
        "wednesday": True,
        "thursday": True,
        "friday": True,
        "saturday": False,
        "sunday": False,
        "start_date": "20260101",
        "end_date": "20261231",
    }


@pytest.fixture
def sample_journey_pattern() -> Dict[str, Any]:
    return {
        "id": "JP_T1",
        "line_id": "L1",
        "direction": DirectionType.OUTBOUND,
    }


@pytest.fixture
def sample_service_journey() -> Dict[str, Any]:
    return {
        "id": "T1",
        "line_id": "L1",
        "journey_pattern_id": "JP_T1",
        "day_type_id": "WEEKDAY",
        "departure_time": "08:00:00",
    }


@pytest.fixture
def populated_db(
    db, sample_operator, sample_stops, sample_line,
    sample_day_type, sample_journey_pattern, sample_service_journey,
) -> TransmodelDatabase:
    """A database seeded with a minimal valid transit network."""
    db.upsert("operator", [sample_operator])
    db.upsert("scheduled_stop_point", sample_stops)
    db.upsert("line", [sample_line])
    db.upsert("day_type", [sample_day_type])
    db.upsert("journey_pattern", [sample_journey_pattern])
    db.upsert("service_journey", [sample_service_journey])
    db.upsert("point_in_journey_pattern", [
        {"journey_pattern_id": "JP_T1", "stop_point_id": "S1", "order": 1},
        {"journey_pattern_id": "JP_T1", "stop_point_id": "S2", "order": 2},
    ])
    db.upsert("passing_time", [
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 2,
         "arrival_time": "08:10:00", "departure_time": "08:10:00"},
    ])
    return db


@pytest.fixture
def minimal_gtfs_records() -> Dict[str, List[Dict[str, Any]]]:
    """Minimal GTFS CSV records for building a test zip."""
    return {
        "agency": [{
            "agency_id": "A1",
            "agency_name": "Test Agency",
            "agency_url": "https://example.com",
            "agency_timezone": "UTC",
        }],
        "stops": [
            {"stop_id": "S1", "stop_name": "Stop 1",
             "stop_lat": "40.0", "stop_lon": "-74.0", "location_type": "0"},
            {"stop_id": "S2", "stop_name": "Stop 2",
             "stop_lat": "40.1", "stop_lon": "-74.1", "location_type": "0"},
        ],
        "routes": [{
            "route_id": "R1",
            "agency_id": "A1",
            "route_short_name": "1",
            "route_long_name": "Route 1",
            "route_type": "3",
        }],
        "calendar": [{
            "service_id": "WEEKDAY",
            "monday": "1", "tuesday": "1", "wednesday": "1",
            "thursday": "1", "friday": "1",
            "saturday": "0", "sunday": "0",
            "start_date": "20260101",
            "end_date": "20261231",
        }],
        "trips": [{
            "trip_id": "T1",
            "route_id": "R1",
            "service_id": "WEEKDAY",
            "direction_id": "0",
        }],
        "stop_times": [
            {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1",
             "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            {"trip_id": "T1", "stop_id": "S2", "stop_sequence": "2",
             "arrival_time": "08:10:00", "departure_time": "08:10:00"},
        ],
    }


def make_gtfs_zip(records: Dict[str, List[Dict[str, Any]]]) -> bytes:
    """Build an in-memory GTFS zip from table -> row dicts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table, rows in records.items():
            if not rows:
                continue
            cols = list(rows[0].keys())
            lines = [",".join(cols)]
            for row in rows:
                lines.append(",".join(str(row[c]) for c in cols))
            zf.writestr(f"{table}.txt", "\r\n".join(lines))
    return buf.getvalue()
