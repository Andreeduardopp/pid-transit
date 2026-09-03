"""Tests for GTFS-Realtime snapshot ingestion (adapters/gtfs_rt.py)."""

import pytest

pb2 = pytest.importorskip(
    "google.transit.gtfs_realtime_pb2",
    reason="gtfs-realtime-bindings not installed (pip install pid-transit[rt])",
)

from pid_transit.adapters.gtfs_rt import (
    fetch_snapshot,
    match_snapshot,
    parse_feed,
)
from pid_transit.core.dataset import TransitDataset
from pid_transit.core.exceptions import ImportFailedError


# ─────────────────────────────────────────────────────────────────────────────
# Feed builders
# ─────────────────────────────────────────────────────────────────────────────

def _make_feed():
    feed = pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_752_600_000
    return feed


def _add_trip_update(feed, entity_id, trip_id=None, route_id=None,
                     start_time=None, delay=None, relationship=None,
                     stop_updates=()):
    entity = feed.entity.add()
    entity.id = entity_id
    tu = entity.trip_update
    if trip_id:
        tu.trip.trip_id = trip_id
    if route_id:
        tu.trip.route_id = route_id
    if start_time:
        tu.trip.start_time = start_time
    if relationship is not None:
        tu.trip.schedule_relationship = relationship
    if delay is not None:
        tu.delay = delay
    for stop_id, arrival_delay in stop_updates:
        stu = tu.stop_time_update.add()
        stu.stop_id = stop_id
        stu.arrival.delay = arrival_delay
    return tu


def _add_vehicle(feed, entity_id, trip_id=None, lat=None, lon=None,
                 stop_id=None):
    entity = feed.entity.add()
    entity.id = entity_id
    vp = entity.vehicle
    if trip_id:
        vp.trip.trip_id = trip_id
    if lat is not None:
        vp.position.latitude = lat
        vp.position.longitude = lon
    if stop_id:
        vp.stop_id = stop_id
    return vp


def _add_alert(feed, entity_id, header=None, route_ids=(), stop_ids=()):
    entity = feed.entity.add()
    entity.id = entity_id
    alert = entity.alert
    alert.cause = pb2.Alert.CONSTRUCTION
    alert.effect = pb2.Alert.DETOUR
    if header:
        t = alert.header_text.translation.add()
        t.text = header
    for rid in route_ids:
        alert.informed_entity.add().route_id = rid
    for sid in stop_ids:
        alert.informed_entity.add().stop_id = sid
    return alert


# ─────────────────────────────────────────────────────────────────────────────
# Static dataset fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def dataset(sample_operator, sample_stops, sample_line, sample_day_type,
            sample_journey_pattern):
    ds = TransitDataset(":memory:")
    ds.db.upsert("operator", [sample_operator])
    ds.db.upsert("scheduled_stop_point", sample_stops)
    ds.db.upsert("line", [dict(sample_line, transport_mode="bus")])
    ds.db.upsert("day_type", [sample_day_type])
    ds.db.upsert("journey_pattern", [dict(sample_journey_pattern, direction="outbound")])
    ds.db.upsert("service_journey", [
        {"id": "T1", "line_id": "L1", "journey_pattern_id": "JP_T1",
         "day_type_id": "WEEKDAY", "departure_time": "08:00:00"},
        {"id": "T2", "line_id": "L1", "journey_pattern_id": "JP_T1",
         "day_type_id": "WEEKDAY", "departure_time": "08:30:00"},
    ])
    yield ds
    ds.close()


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestParseFeed:
    def test_parses_header(self):
        snapshot = parse_feed(_make_feed().SerializeToString())
        assert snapshot.gtfs_realtime_version == "2.0"
        assert snapshot.feed_timestamp == 1_752_600_000
        assert snapshot.entity_count == 0

    def test_parses_trip_update(self):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="T1", delay=120,
                         stop_updates=[("S1", 60), ("S2", 120)])
        snapshot = parse_feed(feed.SerializeToString())

        assert len(snapshot.trip_updates) == 1
        tu = snapshot.trip_updates[0]
        assert tu.trip.trip_id == "T1"
        assert tu.trip.schedule_relationship == "SCHEDULED"
        assert tu.delay == 120
        assert [s.stop_id for s in tu.stop_time_updates] == ["S1", "S2"]
        assert tu.stop_time_updates[1].arrival_delay == 120

    def test_parses_vehicle_position(self):
        feed = _make_feed()
        _add_vehicle(feed, "v1", trip_id="T1", lat=40.05, lon=-74.05, stop_id="S2")
        snapshot = parse_feed(feed.SerializeToString())

        assert len(snapshot.vehicle_positions) == 1
        vp = snapshot.vehicle_positions[0]
        assert vp.trip.trip_id == "T1"
        assert vp.lat == pytest.approx(40.05)
        assert vp.stop_id == "S2"

    def test_parses_alert(self):
        feed = _make_feed()
        _add_alert(feed, "a1", header="Detour on Line 1",
                   route_ids=["L1"], stop_ids=["S1"])
        snapshot = parse_feed(feed.SerializeToString())

        assert len(snapshot.alerts) == 1
        alert = snapshot.alerts[0]
        assert alert.cause == "CONSTRUCTION"
        assert alert.effect == "DETOUR"
        assert alert.header == "Detour on Line 1"
        assert alert.route_ids == ["L1"]
        assert alert.stop_ids == ["S1"]

    def test_garbage_bytes_raise_import_error(self):
        with pytest.raises(ImportFailedError):
            parse_feed(b"\xff\xfenot a protobuf feed at all")

    def test_absent_optional_fields_are_none(self):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="T1")  # no delay, no stop updates
        tu = parse_feed(feed.SerializeToString()).trip_updates[0]
        assert tu.delay is None
        assert tu.trip.start_time is None
        assert tu.stop_time_updates == []


# ─────────────────────────────────────────────────────────────────────────────
# Fetching from a local file
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchSnapshot:
    def test_reads_local_pb_file(self, tmp_path):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="T1")
        pb_file = tmp_path / "feed.pb"
        pb_file.write_bytes(feed.SerializeToString())

        snapshot = fetch_snapshot(pb_file)
        assert len(snapshot.trip_updates) == 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ImportFailedError, match="not found"):
            fetch_snapshot(tmp_path / "nope.pb")


# ─────────────────────────────────────────────────────────────────────────────
# Matching
# ─────────────────────────────────────────────────────────────────────────────

class TestMatchSnapshot:
    def test_direct_trip_id_match(self, dataset):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="T1", delay=90)
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        assert report.trips_matched == 1
        assert report.trips_unmatched == 0
        assert report.match_rate == 1.0
        assert report.trip_resolution["T1"] == "T1"
        assert report.delay_summary()["avg_secs"] == 90

    def test_fallback_match_by_route_and_start_time(self, dataset):
        feed = _make_feed()
        _add_trip_update(feed, "e1", route_id="L1", start_time="08:30:00")
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        assert report.trips_matched_by_fallback == 1
        assert report.trips_unmatched == 0
        assert report.trip_resolution["e1"] == "T2"

    def test_unmatched_trip_reported(self, dataset):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="GHOST")
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        assert report.trips_unmatched == 1
        assert report.match_rate == 0.0
        assert any(i.category == "unmatched_trip" for i in report.issues)

    def test_added_trip_not_counted_as_unmatched(self, dataset):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="EXTRA_RUN",
                         relationship=pb2.TripDescriptor.ADDED)
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        assert report.trips_added == 1
        assert report.trips_unmatched == 0
        assert report.match_rate is None

    def test_canceled_trip_counted_and_matched(self, dataset):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="T1",
                         relationship=pb2.TripDescriptor.CANCELED)
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        assert report.trips_canceled == 1
        assert report.trips_matched == 1

    def test_unknown_stop_reference_flagged(self, dataset):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="T1", stop_updates=[("NOWHERE", 0)])
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        assert report.unknown_stop_refs == 1
        assert any(i.category == "unknown_stop" for i in report.issues)

    def test_vehicle_matching(self, dataset):
        feed = _make_feed()
        _add_vehicle(feed, "v1", trip_id="T1", lat=40.0, lon=-74.0)
        _add_vehicle(feed, "v2", trip_id="GHOST")
        _add_vehicle(feed, "v3", lat=41.0, lon=-74.2)  # deadheading: no trip assigned
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        assert report.vehicles_matched == 1
        assert report.vehicles_unmatched == 1
        assert report.vehicles_without_trip == 1

    def test_alert_matching(self, dataset):
        feed = _make_feed()
        _add_alert(feed, "a1", route_ids=["L1"])          # known route
        _add_alert(feed, "a2", route_ids=["NO_SUCH"])     # unknown refs only
        _add_alert(feed, "a3")                            # network-wide
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        assert report.alerts_matched == 2
        assert report.alerts_unmatched == 1

    def test_stop_delay_used_when_trip_delay_absent(self, dataset):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="T1",
                         stop_updates=[("S1", 30), ("S2", 150)])
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        # Last stop's arrival delay stands in for the trip-level delay.
        assert report.delays == [150]

    def test_report_serialization(self, dataset):
        feed = _make_feed()
        _add_trip_update(feed, "e1", trip_id="T1", delay=60)
        _add_trip_update(feed, "e2", trip_id="GHOST")
        report = match_snapshot(parse_feed(feed.SerializeToString()), dataset)

        d = report.to_dict()
        assert d["trip_updates"]["matched"] == 1
        assert d["trip_updates"]["unmatched"] == 1
        assert d["trip_updates"]["match_rate"] == 0.5
        assert d["delay_summary"]["count"] == 1

        md = report.to_markdown()
        assert "Trip match rate:" in md
        assert "GHOST" in md
