"""Tests for the Transmodel semantic validator (formalization.md §4).

Structural rules V01–V15 are exercised indirectly by other suites; this file
focuses on the semantic rules V16–V20 (roadmap task 5) with a positive and a
negative fixture for each, plus the ``min_severity`` filter and the NeTEx
exporter's dedup precondition guard.
"""

import logging

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.core.schemas import TransportMode, DirectionType
from pid_transit.core.validator import validate_transmodel, Severity


# --- Seeding helper ---------------------------------------------------------

# Two stops ~14 km apart (0.1° lat + 0.1° lon at latitude 40).
_STOPS = [
    {"id": "S1", "name": "Main St", "lat": 40.0, "lon": -74.0},
    {"id": "S2", "name": "Elm St", "lat": 40.1, "lon": -74.1},
]
_PIJP = [
    {"journey_pattern_id": "JP1", "stop_point_id": "S1", "order": 1},
    {"journey_pattern_id": "JP1", "stop_point_id": "S2", "order": 2},
]
# 14 km in 600 s ≈ 84 km/h — well under the 150 km/h bus ceiling.
_PASSING = [
    {"service_journey_id": "T1", "stop_point_id": "S1", "order": 1,
     "arrival_time": "08:00:00", "departure_time": "08:00:00"},
    {"service_journey_id": "T1", "stop_point_id": "S2", "order": 2,
     "arrival_time": "08:10:00", "departure_time": "08:10:00"},
]


def seed(
    db: TransmodelDatabase,
    *,
    stops=None,
    pijp=None,
    passing=None,
    frequencies=None,
    shapes=None,
    journey_pattern_id="JP1",
    mode=TransportMode.BUS,
) -> TransmodelDatabase:
    """Seed a minimal, valid single-line network, overridable per-table."""
    db.upsert("operator", [{"id": "OP1", "name": "Test", "timezone": "UTC"}])
    db.upsert("scheduled_stop_point", _STOPS if stops is None else stops)
    db.upsert("line", [{"id": "L1", "operator_id": "OP1", "name": "Line 1",
                        "transport_mode": mode}])
    db.upsert("day_type", [{"id": "WEEKDAY", "monday": True,
                            "start_date": "20260101", "end_date": "20261231"}])
    db.upsert("journey_pattern", [{"id": "JP1", "line_id": "L1",
                                   "direction": DirectionType.OUTBOUND}])
    db.upsert("point_in_journey_pattern", _PIJP if pijp is None else pijp)
    db.upsert("service_journey", [{"id": "T1", "line_id": "L1",
                                   "journey_pattern_id": journey_pattern_id,
                                   "day_type_id": "WEEKDAY",
                                   "departure_time": "08:00:00"}])
    db.upsert("passing_time", _PASSING if passing is None else passing)
    if frequencies:
        db.upsert("frequency", frequencies)
    if shapes:
        db.upsert("shape_point", shapes)
    return db


def types_of(report, issue_type):
    return [i for i in report.issues if i.issue_type == issue_type]


# --- Baseline ---------------------------------------------------------------

def test_valid_network_has_no_issues(db):
    report = validate_transmodel(seed(db))
    assert report.is_valid
    assert report.issues == []


# --- V16: travel-speed plausibility ----------------------------------------

def test_v16_implausible_speed_is_error(db):
    # 14 km covered in 30 s → ~1680 km/h, far above the bus ceiling.
    fast = [
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 2,
         "arrival_time": "08:00:30", "departure_time": "08:00:30"},
    ]
    report = validate_transmodel(seed(db, passing=fast))
    issues = types_of(report, "implausible_speed")
    assert issues and issues[0].severity == Severity.ERROR
    assert not report.is_valid


def test_v16_zero_time_over_distance_is_warning(db):
    # Distinct, distant stops sharing the same clock time (Δt = 0).
    teleport = [
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 2,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
    ]
    report = validate_transmodel(seed(db, passing=teleport))
    issues = types_of(report, "implausible_speed")
    assert issues and all(i.severity == Severity.WARNING for i in issues)


def test_v16_skips_null_coordinates(db):
    stops = [
        {"id": "S1", "name": "Main St", "lat": None, "lon": None},
        {"id": "S2", "name": "Elm St", "lat": 40.1, "lon": -74.1},
    ]
    # Even at an impossible speed, a NULL-coordinate pair must be skipped.
    fast = [
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 2,
         "arrival_time": "08:00:01", "departure_time": "08:00:01"},
    ]
    report = validate_transmodel(seed(db, stops=stops, passing=fast))
    assert types_of(report, "implausible_speed") == []


def test_v16_respects_per_mode_ceiling(db):
    # 14 km in 200 s ≈ 252 km/h: fine for rail (350), an error for tram (120).
    fast = [
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 2,
         "arrival_time": "08:03:20", "departure_time": "08:03:20"},
    ]
    assert validate_transmodel(
        seed(db, passing=fast, mode=TransportMode.RAIL)).is_valid
    db2 = TransmodelDatabase(":memory:")
    report = validate_transmodel(seed(db2, passing=fast, mode=TransportMode.TRAM))
    assert types_of(report, "implausible_speed")


# --- V17: pattern / passing-time consistency -------------------------------

def test_v17_sequence_mismatch_is_error(db):
    # Pattern is S1→S2 but the journey visits S2→S1.
    swapped = [
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 2,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
    ]
    report = validate_transmodel(seed(db, passing=swapped))
    issues = types_of(report, "pattern_sequence_mismatch")
    assert issues and issues[0].severity == Severity.ERROR
    assert not report.is_valid


def test_v17_matching_sequence_passes(db):
    report = validate_transmodel(seed(db))
    assert types_of(report, "pattern_sequence_mismatch") == []


# --- V18: duplicate-stop detection -----------------------------------------

def test_v18_flags_same_name_within_radius(db):
    stops = [
        {"id": "S1", "name": "Central Plaza", "lat": 40.0, "lon": -74.0},
        # ~8 m north of S1, identical (case-insensitive) name.
        {"id": "S2", "name": "central plaza", "lat": 40.00007, "lon": -74.0},
    ]
    report = validate_transmodel(seed(db, stops=stops))
    issues = types_of(report, "duplicate_stop")
    assert issues and issues[0].severity == Severity.WARNING


def test_v18_ignores_distant_or_differently_named_stops(db):
    # Same coords baseline (S1/S2 14 km apart, different names) → no dup.
    assert types_of(validate_transmodel(seed(db)), "duplicate_stop") == []
    # Same name but far apart → still no dup.
    stops = [
        {"id": "S1", "name": "Depot", "lat": 40.0, "lon": -74.0},
        {"id": "S2", "name": "Depot", "lat": 41.0, "lon": -74.0},
    ]
    db2 = TransmodelDatabase(":memory:")
    assert types_of(
        validate_transmodel(seed(db2, stops=stops)), "duplicate_stop") == []


# --- V19: unused entities ---------------------------------------------------

def test_v19_reports_unused_entities_as_info(db):
    seed(db)
    # An orphan stop, an orphan pattern, an orphan day type, an orphan shape.
    db.upsert("scheduled_stop_point",
              [{"id": "S_ORPHAN", "name": "Nowhere", "lat": 5.0, "lon": 5.0}])
    db.upsert("journey_pattern",
              [{"id": "JP_ORPHAN", "line_id": "L1", "direction": DirectionType.INBOUND}])
    db.upsert("day_type", [{"id": "DT_ORPHAN", "sunday": True,
                            "start_date": "20260101", "end_date": "20261231"}])
    db.upsert("shape_point", [{"shape_id": "SH_ORPHAN", "lat": 40.0,
                               "lon": -74.0, "sequence": 1}])
    report = validate_transmodel(db)
    kinds = {i.issue_type for i in report.issues}
    assert {"unused_stop", "unused_pattern", "unused_day_type",
            "unused_shape"} <= kinds
    assert all(i.severity == Severity.INFO
               for i in report.issues if i.issue_type.startswith("unused_"))
    # Info-only orphans must not invalidate the feed.
    assert report.is_valid


# --- V20: frequency sanity --------------------------------------------------

def test_v20_reversed_window_and_bad_headway(db):
    freqs = [
        {"service_journey_id": "T1", "start_time": "18:00:00",
         "end_time": "06:00:00", "headway_secs": 600},   # reversed window
        {"service_journey_id": "T1", "start_time": "06:00:00",
         "end_time": "07:00:00", "headway_secs": 0},      # non-positive headway
    ]
    report = validate_transmodel(seed(db, frequencies=freqs))
    assert types_of(report, "frequency_window")
    assert types_of(report, "frequency_headway")


def test_v20_overlapping_windows(db):
    freqs = [
        {"service_journey_id": "T1", "start_time": "06:00:00",
         "end_time": "09:00:00", "headway_secs": 600},
        {"service_journey_id": "T1", "start_time": "08:00:00",
         "end_time": "10:00:00", "headway_secs": 600},   # overlaps the first
    ]
    report = validate_transmodel(seed(db, frequencies=freqs))
    assert types_of(report, "frequency_overlap")


def test_v20_valid_frequencies_pass(db):
    freqs = [
        {"service_journey_id": "T1", "start_time": "06:00:00",
         "end_time": "09:00:00", "headway_secs": 600},
        {"service_journey_id": "T1", "start_time": "09:00:00",
         "end_time": "12:00:00", "headway_secs": 1200},
    ]
    report = validate_transmodel(seed(db, frequencies=freqs))
    assert types_of(report, "frequency_window") == []
    assert types_of(report, "frequency_headway") == []
    assert types_of(report, "frequency_overlap") == []


# --- min_severity filter ----------------------------------------------------

def test_min_severity_filters_but_preserves_is_valid(db):
    # Manufacture one issue at each severity (the DB enforces FKs, so orphan-
    # reference errors aren't reachable; use a purely logical error instead):
    #   ERROR   — pattern/passing-time mismatch (V17)
    #   WARNING — duplicate stop (V18)
    #   INFO    — unused stop (V19); S3/S4 aren't on any journey
    swapped = [
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 2,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
    ]
    seed(db, passing=swapped)
    db.upsert("scheduled_stop_point", [
        {"id": "S3", "name": "Twin", "lat": 40.0, "lon": -74.0},
        {"id": "S4", "name": "twin", "lat": 40.00007, "lon": -74.0},
    ])

    full = validate_transmodel(db)
    severities = {i.severity for i in full.issues}
    assert {Severity.ERROR, Severity.WARNING, Severity.INFO} <= severities
    assert not full.is_valid

    warned = validate_transmodel(db, min_severity=Severity.WARNING)
    assert all(i.severity != Severity.INFO for i in warned.issues)
    assert not warned.is_valid  # is_valid still reflects the filtered-in error

    errors_only = validate_transmodel(db, min_severity=Severity.ERROR)
    assert all(i.severity == Severity.ERROR for i in errors_only.issues)


# --- NeTEx exporter dedup precondition guard (acceptance criterion #2) ------

def test_netex_export_warns_on_pattern_violation(db, tmp_path, caplog):
    from pid_transit.adapters.netex_exporter import NetexExporter

    swapped = [
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 1,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 2,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
    ]
    seed(db, passing=swapped)
    out = tmp_path / "out.xml"
    with caplog.at_level(logging.WARNING,
                         logger="pid_transit.adapters.netex_exporter"):
        NetexExporter(deduplicate_patterns=True).export_from_db(db, out)
    assert any("V17" in r.message or "do not" in r.message for r in caplog.records)


def test_netex_export_no_warning_when_consistent(db, tmp_path, caplog):
    from pid_transit.adapters.netex_exporter import NetexExporter

    seed(db)
    out = tmp_path / "out.xml"
    with caplog.at_level(logging.WARNING,
                         logger="pid_transit.adapters.netex_exporter"):
        NetexExporter(deduplicate_patterns=True).export_from_db(db, out)
    assert not any("V17" in r.message for r in caplog.records)
