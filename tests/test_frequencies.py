"""Tests for frequency expansion / compression (roadmap task 8.4)."""

import pytest

from pid_transit.core.dataset import TransitDataset
from pid_transit.core.database import TransmodelDatabase
from pid_transit.core.schemas import TransportMode, DirectionType
from pid_transit.core.frequencies import (
    expand_frequencies, compress_to_frequencies)


# --- Seeding ----------------------------------------------------------------

def _seed_base(db: TransmodelDatabase) -> None:
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


def _fmt(s):
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _add_journey(db, jid, dep_sec, ride=600):
    """A journey departing at dep_sec, reaching S2 `ride` seconds later."""
    db.upsert("service_journey", [{"id": jid, "line_id": "L1",
                                   "journey_pattern_id": "JP1", "day_type_id": "WD",
                                   "departure_time": _fmt(dep_sec)}])
    db.upsert("passing_time", [
        {"service_journey_id": jid, "stop_point_id": "S1", "order": 1,
         "arrival_time": _fmt(dep_sec), "departure_time": _fmt(dep_sec)},
        {"service_journey_id": jid, "stop_point_id": "S2", "order": 2,
         "arrival_time": _fmt(dep_sec + ride), "departure_time": _fmt(dep_sec + ride)}])


def _departure_set(db):
    return sorted(sj["departure_time"] for sj in db.get_records("service_journey"))


# --- expand -----------------------------------------------------------------

def test_expand_materializes_departures(db):
    _seed_base(db)
    _add_journey(db, "T", 6 * 3600)  # template at 06:00
    db.upsert("frequency", [{"service_journey_id": "T", "start_time": "06:00:00",
                             "end_time": "06:30:00", "headway_secs": 600,
                             "exact_times": 0}])

    result = expand_frequencies(db)

    assert result["frequencies_expanded"] == 1
    assert result["journeys_created"] == 3           # 06:00, 06:10, 06:20 (end excl.)
    assert db.count("frequency") == 0                # frequency consumed
    assert db.get_one("service_journey", {"id": "T"}) is None  # template replaced
    assert _departure_set(db) == ["06:00:00", "06:10:00", "06:20:00"]

    # passing times shifted by each departure's offset
    pt = db.get_one("passing_time",
                    {"service_journey_id": "T#1", "stop_point_id": "S2"})
    assert pt["departure_time"] == "06:20:00"        # 06:10 + 10 min ride


def test_expand_no_frequencies_is_noop(db):
    _seed_base(db)
    _add_journey(db, "T1", 6 * 3600)
    result = expand_frequencies(db)
    assert result == {"frequencies_expanded": 0, "journeys_created": 0,
                      "passing_times_created": 0}
    assert db.count("service_journey") == 1


# --- compress ---------------------------------------------------------------

def test_compress_collapses_regular_run(db):
    _seed_base(db)
    for i in range(5):                                 # 06:00 … 06:40, every 10 min
        _add_journey(db, f"T{i}", 6 * 3600 + i * 600)

    result = compress_to_frequencies(db, min_run=3)

    assert result["runs_compressed"] == 1
    assert result["journeys_removed"] == 4
    assert result["frequencies_created"] == 1
    assert db.count("service_journey") == 1            # only the template remains
    freq = db.get_records("frequency")[0]
    assert freq["start_time"] == "06:00:00"
    assert freq["end_time"] == "06:50:00"              # d_last + h, exclusive
    assert freq["headway_secs"] == 600
    assert freq["exact_times"] == 1


def test_compress_leaves_irregular_departures(db):
    _seed_base(db)
    _add_journey(db, "T0", 6 * 3600)          # 06:00
    _add_journey(db, "T1", 6 * 3600 + 600)    # 06:10
    _add_journey(db, "T2", 6 * 3600 + 1500)   # 06:25 — breaks the progression

    result = compress_to_frequencies(db, min_run=3)
    assert result["runs_compressed"] == 0
    assert db.count("service_journey") == 3
    assert db.count("frequency") == 0


def test_compress_respects_min_run(db):
    _seed_base(db)
    for i in range(3):                                 # exactly 3 regular departures
        _add_journey(db, f"T{i}", 6 * 3600 + i * 600)
    assert compress_to_frequencies(db, min_run=4)["runs_compressed"] == 0
    assert db.count("service_journey") == 3


def test_compress_separates_distinct_profiles(db):
    _seed_base(db)
    # Same headway, but two different ride times → two timing profiles θ.
    for i in range(3):
        _add_journey(db, f"A{i}", 6 * 3600 + i * 600, ride=600)
    for i in range(3):
        _add_journey(db, f"B{i}", 7 * 3600 + i * 600, ride=900)

    result = compress_to_frequencies(db, min_run=3)
    assert result["runs_compressed"] == 2              # one per profile
    assert db.count("frequency") == 2


# --- round-trip (acceptance) ------------------------------------------------

def test_expand_compress_roundtrip_preserves_departures(db):
    _seed_base(db)
    for i in range(6):                                 # 06:00 … 06:50
        _add_journey(db, f"T{i}", 6 * 3600 + i * 600)
    before = _departure_set(db)
    before_pts = {
        (p["stop_point_id"], p["departure_time"])
        for p in db.get_records("passing_time")
    }

    compress_to_frequencies(db, min_run=3)
    assert db.count("frequency") == 1                  # actually compressed
    expand_frequencies(db)

    assert _departure_set(db) == before                # departures reproduced exactly
    after_pts = {
        (p["stop_point_id"], p["departure_time"])
        for p in db.get_records("passing_time")
    }
    assert after_pts == before_pts                     # and passing times too


# --- dataset delegation + stats dividend -------------------------------------

def test_dataset_methods_and_stats_dividend():
    from pid_transit.analytics.statistics import TransitStatistics

    ds = TransitDataset(":memory:")
    _seed_base(ds.db)
    _add_journey(ds.db, "T", 6 * 3600)
    ds.db.upsert("frequency", [{"service_journey_id": "T", "start_time": "06:00:00",
                                "end_time": "07:00:00", "headway_secs": 600,
                                "exact_times": 1}])

    # Before expansion a single template journey undercounts vehicle-hours.
    vh_before = TransitStatistics(ds).vehicle_hours(day_type_id="WD")
    result = ds.expand_frequencies()
    vh_after = TransitStatistics(ds).vehicle_hours(day_type_id="WD")

    assert result["journeys_created"] == 6             # 06:00 … 06:50
    assert vh_after == pytest.approx(6 * vh_before)     # six real departures now
