"""Tests for TransitDataset ergonomics (roadmap task 7 API items) and the
pattern-normalization operation (roadmap task 6 part A)."""

import gc

import pytest

from pid_transit.core.dataset import TransitDataset
from pid_transit.core.database import TransmodelDatabase
from pid_transit.core.patterns import build_pattern_dedup_map
from pid_transit.core.schemas import TransportMode, DirectionType
from tests.conftest import make_gtfs_zip


# --- Task 7: context manager + convenience constructors --------------------

def _zip(records, tmp_path, name="feed.zip"):
    p = tmp_path / name
    p.write_bytes(make_gtfs_zip(records))
    return p


def test_context_manager_releases_file_handle(minimal_gtfs_records, tmp_path):
    db_path = tmp_path / "cm.db"
    with TransitDataset.from_gtfs(str(_zip(minimal_gtfs_records, tmp_path)),
                                  db=str(db_path)) as ds:
        assert ds.lines.count() >= 1
    # On Windows an open handle blocks deletion; the with-block must have freed it.
    gc.collect()
    db_path.unlink()
    assert not db_path.exists()


def test_from_gtfs_in_memory_by_default(minimal_gtfs_records, tmp_path):
    ds = TransitDataset.from_gtfs(str(_zip(minimal_gtfs_records, tmp_path)))
    assert ds.operators.count() >= 1
    assert ds.lines.count() >= 1


def test_from_netex_roundtrips_through_to_netex(minimal_gtfs_records, tmp_path):
    src = TransitDataset.from_gtfs(str(_zip(minimal_gtfs_records, tmp_path)))
    netex = tmp_path / "feed.xml"
    src.to_netex(netex, deduplicate_patterns=False)
    assert netex.exists()

    ds = TransitDataset.from_netex(str(netex))
    assert ds.service_journeys.count() == src.service_journeys.count()


def test_to_gtfs_writes_zip(minimal_gtfs_records, tmp_path):
    ds = TransitDataset.from_gtfs(str(_zip(minimal_gtfs_records, tmp_path)))
    out = tmp_path / "out.zip"
    ds.to_gtfs(out, include_shapes=False)
    assert out.exists()


# --- Task 6 part A: normalize_patterns -------------------------------------

def _two_identical_pattern_feed():
    """Two trips on the same route/direction/stops → two synthetic patterns
    that collapse to one canonical class."""
    return {
        "agency": [{"agency_id": "A1", "agency_name": "A",
                    "agency_url": "https://x.y", "agency_timezone": "UTC"}],
        "stops": [
            {"stop_id": "S1", "stop_name": "S1", "stop_lat": "40.0",
             "stop_lon": "-74.0", "location_type": "0"},
            {"stop_id": "S2", "stop_name": "S2", "stop_lat": "40.1",
             "stop_lon": "-74.1", "location_type": "0"},
        ],
        "routes": [{"route_id": "R1", "agency_id": "A1", "route_short_name": "1",
                    "route_long_name": "R1", "route_type": "3"}],
        "calendar": [{"service_id": "WD", "monday": "1", "tuesday": "1",
                      "wednesday": "1", "thursday": "1", "friday": "1",
                      "saturday": "0", "sunday": "0",
                      "start_date": "20260101", "end_date": "20261231"}],
        "trips": [
            {"trip_id": "T1", "route_id": "R1", "service_id": "WD", "direction_id": "0"},
            {"trip_id": "T2", "route_id": "R1", "service_id": "WD", "direction_id": "0"},
        ],
        "stop_times": [
            {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1",
             "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            {"trip_id": "T1", "stop_id": "S2", "stop_sequence": "2",
             "arrival_time": "08:10:00", "departure_time": "08:10:00"},
            {"trip_id": "T2", "stop_id": "S1", "stop_sequence": "1",
             "arrival_time": "09:00:00", "departure_time": "09:00:00"},
            {"trip_id": "T2", "stop_id": "S2", "stop_sequence": "2",
             "arrival_time": "09:10:00", "departure_time": "09:10:00"},
        ],
    }


def test_normalize_collapses_to_dedup_quotient(tmp_path):
    ds = TransitDataset.from_gtfs(str(_zip(_two_identical_pattern_feed(), tmp_path)))
    # What the NeTEx exporter's dedup would keep, computed on the original DB.
    _, canonical_ids = build_pattern_dedup_map(ds.db)
    assert ds.journey_patterns.count() == 2  # two synthetic patterns pre-normalize

    result = ds.normalize_patterns()

    assert result["patterns_before"] == 2
    assert result["patterns_after"] == len(canonical_ids) == 1
    assert result["journeys_remapped"] == 1
    # The surviving patterns are exactly the dedup canonical set...
    surviving = {jp.id for jp in ds.journey_patterns.get_all()}
    assert surviving == canonical_ids
    # ...and every journey now points at a surviving (canonical) pattern.
    for sj in ds.service_journeys.get_all():
        assert sj.journey_pattern_id in surviving


def test_normalize_preserves_journey_semantics(tmp_path):
    ds = TransitDataset.from_gtfs(str(_zip(_two_identical_pattern_feed(), tmp_path)))
    before = ds.service_journeys.count(), ds.passing_times.count()
    ds.normalize_patterns()
    after = ds.service_journeys.count(), ds.passing_times.count()
    assert before == after  # only pattern rows change, not journeys/times


def test_normalize_refuses_on_pattern_violation():
    # Build a DB whose journey violates its pattern (V17), then expect a refusal.
    ds = TransitDataset(":memory:")
    db = ds.db
    db.upsert("operator", [{"id": "OP1", "name": "T", "timezone": "UTC"}])
    db.upsert("scheduled_stop_point", [
        {"id": "S1", "name": "A", "lat": 40.0, "lon": -74.0},
        {"id": "S2", "name": "B", "lat": 40.1, "lon": -74.1}])
    db.upsert("line", [{"id": "L1", "operator_id": "OP1", "name": "L",
                        "transport_mode": TransportMode.BUS}])
    db.upsert("day_type", [{"id": "WD", "monday": True,
                            "start_date": "20260101", "end_date": "20261231"}])
    db.upsert("journey_pattern", [{"id": "JP1", "line_id": "L1",
                                   "direction": DirectionType.OUTBOUND}])
    db.upsert("point_in_journey_pattern", [
        {"journey_pattern_id": "JP1", "stop_point_id": "S1", "order": 1},
        {"journey_pattern_id": "JP1", "stop_point_id": "S2", "order": 2}])
    db.upsert("service_journey", [{"id": "T1", "line_id": "L1",
                                   "journey_pattern_id": "JP1", "day_type_id": "WD",
                                   "departure_time": "08:00:00"}])
    db.upsert("passing_time", [  # visits S2 then S1 — disagrees with the pattern
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 2,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"}])

    with pytest.raises(ValueError, match="V17"):
        ds.normalize_patterns()
