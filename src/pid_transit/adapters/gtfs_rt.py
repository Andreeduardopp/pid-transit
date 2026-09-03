"""
GTFS-Realtime snapshot ingestion.

Real-time data is a stream of ephemeral overlays on the static Transmodel
entities, not another file format — so this module deliberately does NOT
implement the importer contract (`import_to_db`) and writes nothing to the
database. The unit of work is an `RtSnapshot`: one fetched-and-parsed
`FeedMessage`, matched in memory against a loaded `TransitDataset`
(trip_id → ServiceJourney, stop_id → ScheduledStopPoint, route_id → Line).
Continuous polling is left to the caller (or `pid-transit rt-snapshot`).

Requires the optional `rt` extra: `pip install pid-transit[rt]`
(pulls in `gtfs-realtime-bindings` for protobuf parsing).
"""

import json
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.dataset import TransitDataset
from ..core.exceptions import ImportFailedError


def _pb2():
    """Import the protobuf bindings lazily so the core install stays lean."""
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError as e:
        raise ImportFailedError(
            "GTFS-Realtime support requires the 'gtfs-realtime-bindings' "
            "package. Install it with: pip install pid-transit[rt]"
        ) from e
    return gtfs_realtime_pb2


# ═══════════════════════════════════════════════════════════════════════════
# Snapshot models (ephemeral — never persisted to the Transmodel schema)
# ═══════════════════════════════════════════════════════════════════════════

class RtTripDescriptor(BaseModel):
    """GTFS-RT TripDescriptor: identifies which ServiceJourney this refers to."""
    trip_id: Optional[str] = Field(None, description="GTFS trip_id / ServiceJourney.id")
    route_id: Optional[str] = Field(None, description="GTFS route_id / Line.id")
    direction_id: Optional[int] = Field(None, description="GTFS direction_id (0/1)")
    start_time: Optional[str] = Field(None, description="Trip start HH:MM:SS (frequency disambiguator)")
    start_date: Optional[str] = Field(None, description="Service date YYYYMMDD")
    schedule_relationship: str = Field("SCHEDULED", description="SCHEDULED/ADDED/CANCELED/...")


class RtStopTimeUpdate(BaseModel):
    """GTFS-RT StopTimeUpdate: predicted times at one stop of the trip."""
    stop_sequence: Optional[int] = Field(None, description="Matches PassingTime.order")
    stop_id: Optional[str] = Field(None, description="ScheduledStopPoint.id")
    arrival_delay: Optional[int] = Field(None, description="Arrival delay in seconds")
    arrival_time: Optional[int] = Field(None, description="Predicted arrival (POSIX)")
    departure_delay: Optional[int] = Field(None, description="Departure delay in seconds")
    departure_time: Optional[int] = Field(None, description="Predicted departure (POSIX)")
    schedule_relationship: str = Field("SCHEDULED", description="SCHEDULED/SKIPPED/NO_DATA")


class RtTripUpdate(BaseModel):
    """GTFS-RT TripUpdate: real-time progress of one ServiceJourney."""
    entity_id: str = Field(..., description="FeedEntity id")
    trip: RtTripDescriptor
    vehicle_id: Optional[str] = Field(None, description="VehicleDescriptor.id")
    vehicle_label: Optional[str] = Field(None, description="VehicleDescriptor.label")
    delay: Optional[int] = Field(None, description="Trip-level delay in seconds")
    timestamp: Optional[int] = Field(None, description="Measurement time (POSIX)")
    stop_time_updates: List[RtStopTimeUpdate] = Field(default_factory=list)


class RtVehiclePosition(BaseModel):
    """GTFS-RT VehiclePosition: where a vehicle is right now."""
    entity_id: str = Field(..., description="FeedEntity id")
    trip: Optional[RtTripDescriptor] = Field(None, description="Trip served, if assigned")
    vehicle_id: Optional[str] = Field(None, description="VehicleDescriptor.id")
    vehicle_label: Optional[str] = Field(None, description="VehicleDescriptor.label")
    lat: Optional[float] = Field(None, description="Latitude")
    lon: Optional[float] = Field(None, description="Longitude")
    bearing: Optional[float] = Field(None, description="Heading in degrees")
    speed: Optional[float] = Field(None, description="Speed in m/s")
    stop_id: Optional[str] = Field(None, description="Current/next stop")
    current_stop_sequence: Optional[int] = Field(None, description="Index into the pattern")
    current_status: Optional[str] = Field(None, description="IN_TRANSIT_TO/STOPPED_AT/...")
    occupancy_status: Optional[str] = Field(None, description="EMPTY/FULL/...")
    timestamp: Optional[int] = Field(None, description="Measurement time (POSIX)")


class RtAlert(BaseModel):
    """GTFS-RT Alert: a service disruption affecting routes/stops/trips."""
    entity_id: str = Field(..., description="FeedEntity id")
    cause: str = Field("UNKNOWN_CAUSE")
    effect: str = Field("UNKNOWN_EFFECT")
    header: Optional[str] = Field(None, description="First translation of header_text")
    description: Optional[str] = Field(None, description="First translation of description_text")
    route_ids: List[str] = Field(default_factory=list, description="Affected Line ids")
    stop_ids: List[str] = Field(default_factory=list, description="Affected stop ids")
    trip_ids: List[str] = Field(default_factory=list, description="Affected ServiceJourney ids")


class RtSnapshot(BaseModel):
    """One parsed GTFS-RT FeedMessage."""
    feed_timestamp: Optional[int] = Field(None, description="FeedHeader.timestamp (POSIX)")
    gtfs_realtime_version: Optional[str] = Field(None, description="FeedHeader version")
    trip_updates: List[RtTripUpdate] = Field(default_factory=list)
    vehicle_positions: List[RtVehiclePosition] = Field(default_factory=list)
    alerts: List[RtAlert] = Field(default_factory=list)

    @property
    def entity_count(self) -> int:
        return len(self.trip_updates) + len(self.vehicle_positions) + len(self.alerts)


# ═══════════════════════════════════════════════════════════════════════════
# Parsing and fetching
# ═══════════════════════════════════════════════════════════════════════════

def _enum_name(descriptor, value: int) -> str:
    try:
        return descriptor.Name(value)
    except ValueError:
        return str(value)


def _trip_descriptor(pb, trip) -> RtTripDescriptor:
    return RtTripDescriptor(
        trip_id=trip.trip_id or None,
        route_id=trip.route_id or None,
        direction_id=trip.direction_id if trip.HasField("direction_id") else None,
        start_time=trip.start_time or None,
        start_date=trip.start_date or None,
        schedule_relationship=_enum_name(
            pb.TripDescriptor.ScheduleRelationship, trip.schedule_relationship),
    )


def _first_translation(translated) -> Optional[str]:
    for t in translated.translation:
        if t.text:
            return t.text
    return None


def parse_feed(data: bytes) -> RtSnapshot:
    """Parse a serialized GTFS-RT FeedMessage into an RtSnapshot."""
    pb = _pb2()
    feed = pb.FeedMessage()
    try:
        feed.ParseFromString(data)
    except Exception as e:
        raise ImportFailedError(f"Not a valid GTFS-RT protobuf feed: {e}") from e

    snapshot = RtSnapshot(
        feed_timestamp=feed.header.timestamp or None,
        gtfs_realtime_version=feed.header.gtfs_realtime_version or None,
    )

    for entity in feed.entity:
        if entity.HasField("trip_update"):
            tu = entity.trip_update
            stus = []
            for stu in tu.stop_time_update:
                stus.append(RtStopTimeUpdate(
                    stop_sequence=stu.stop_sequence if stu.HasField("stop_sequence") else None,
                    stop_id=stu.stop_id or None,
                    arrival_delay=stu.arrival.delay if stu.HasField("arrival") and stu.arrival.HasField("delay") else None,
                    arrival_time=stu.arrival.time if stu.HasField("arrival") and stu.arrival.HasField("time") else None,
                    departure_delay=stu.departure.delay if stu.HasField("departure") and stu.departure.HasField("delay") else None,
                    departure_time=stu.departure.time if stu.HasField("departure") and stu.departure.HasField("time") else None,
                    schedule_relationship=_enum_name(
                        pb.TripUpdate.StopTimeUpdate.ScheduleRelationship,
                        stu.schedule_relationship),
                ))
            snapshot.trip_updates.append(RtTripUpdate(
                entity_id=entity.id,
                trip=_trip_descriptor(pb, tu.trip),
                vehicle_id=tu.vehicle.id or None,
                vehicle_label=tu.vehicle.label or None,
                delay=tu.delay if tu.HasField("delay") else None,
                timestamp=tu.timestamp or None,
                stop_time_updates=stus,
            ))

        if entity.HasField("vehicle"):
            vp = entity.vehicle
            snapshot.vehicle_positions.append(RtVehiclePosition(
                entity_id=entity.id,
                trip=_trip_descriptor(pb, vp.trip) if vp.HasField("trip") else None,
                vehicle_id=vp.vehicle.id or None,
                vehicle_label=vp.vehicle.label or None,
                lat=vp.position.latitude if vp.HasField("position") else None,
                lon=vp.position.longitude if vp.HasField("position") else None,
                bearing=vp.position.bearing if vp.HasField("position") and vp.position.HasField("bearing") else None,
                speed=vp.position.speed if vp.HasField("position") and vp.position.HasField("speed") else None,
                stop_id=vp.stop_id or None,
                current_stop_sequence=vp.current_stop_sequence if vp.HasField("current_stop_sequence") else None,
                current_status=_enum_name(pb.VehiclePosition.VehicleStopStatus, vp.current_status),
                occupancy_status=_enum_name(pb.VehiclePosition.OccupancyStatus, vp.occupancy_status)
                    if vp.HasField("occupancy_status") else None,
                timestamp=vp.timestamp or None,
            ))

        if entity.HasField("alert"):
            al = entity.alert
            route_ids, stop_ids, trip_ids = [], [], []
            for sel in al.informed_entity:
                if sel.route_id:
                    route_ids.append(sel.route_id)
                if sel.stop_id:
                    stop_ids.append(sel.stop_id)
                if sel.HasField("trip") and sel.trip.trip_id:
                    trip_ids.append(sel.trip.trip_id)
            snapshot.alerts.append(RtAlert(
                entity_id=entity.id,
                cause=_enum_name(pb.Alert.Cause, al.cause),
                effect=_enum_name(pb.Alert.Effect, al.effect),
                header=_first_translation(al.header_text),
                description=_first_translation(al.description_text),
                route_ids=route_ids,
                stop_ids=stop_ids,
                trip_ids=trip_ids,
            ))

    return snapshot


def fetch_snapshot(
    source: str | Path,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
) -> RtSnapshot:
    """Fetch and parse a GTFS-RT feed from a URL or a local .pb file.

    `headers` carries API-key headers many feeds require
    (e.g. Golemio's `X-Access-Token`).
    """
    src = str(source)
    if src.startswith(("http://", "https://")):
        request = urllib.request.Request(src, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
        except Exception as e:
            raise ImportFailedError(f"Failed to fetch GTFS-RT feed from {src}: {e}") from e
    else:
        path = Path(source)
        if not path.exists():
            raise ImportFailedError(f"GTFS-RT source not found: {source}")
        data = path.read_bytes()
    return parse_feed(data)


# ═══════════════════════════════════════════════════════════════════════════
# Matching against the static dataset
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RtMatchIssue:
    category: str
    entity_type: str
    entity_id: str
    message: str


@dataclass
class RtMatchReport:
    """Result of joining an RtSnapshot onto a static TransitDataset."""
    feed_timestamp: Optional[int] = None
    trips_matched: int = 0
    trips_matched_by_fallback: int = 0
    trips_added: int = 0
    trips_canceled: int = 0
    trips_unmatched: int = 0
    vehicles_matched: int = 0
    vehicles_without_trip: int = 0
    vehicles_unmatched: int = 0
    alerts_matched: int = 0
    alerts_unmatched: int = 0
    unknown_stop_refs: int = 0
    delays: List[int] = field(default_factory=list)
    issues: List[RtMatchIssue] = field(default_factory=list)
    # trip_id (or entity id for ADDED trips) → matched ServiceJourney id
    trip_resolution: Dict[str, str] = field(default_factory=dict)

    @property
    def match_rate(self) -> Optional[float]:
        """Share of scheduled (non-ADDED) trip updates resolved to a journey."""
        scheduled = self.trips_matched + self.trips_matched_by_fallback + self.trips_unmatched
        if scheduled == 0:
            return None
        return (self.trips_matched + self.trips_matched_by_fallback) / scheduled

    def delay_summary(self) -> Optional[Dict[str, float]]:
        if not self.delays:
            return None
        return {
            "count": len(self.delays),
            "avg_secs": sum(self.delays) / len(self.delays),
            "min_secs": min(self.delays),
            "max_secs": max(self.delays),
        }

    def to_dict(self) -> Dict:
        rate = self.match_rate
        return {
            "feed_timestamp": self.feed_timestamp,
            "trip_updates": {
                "matched": self.trips_matched,
                "matched_by_fallback": self.trips_matched_by_fallback,
                "added": self.trips_added,
                "canceled": self.trips_canceled,
                "unmatched": self.trips_unmatched,
                "match_rate": round(rate, 4) if rate is not None else None,
            },
            "vehicle_positions": {
                "matched": self.vehicles_matched,
                "without_trip": self.vehicles_without_trip,
                "unmatched": self.vehicles_unmatched,
            },
            "alerts": {
                "matched": self.alerts_matched,
                "unmatched": self.alerts_unmatched,
            },
            "unknown_stop_refs": self.unknown_stop_refs,
            "delay_summary": self.delay_summary(),
            "issue_count": len(self.issues),
            "issues": [
                {"category": i.category, "entity_type": i.entity_type,
                 "entity_id": i.entity_id, "message": i.message}
                for i in self.issues
            ],
        }

    def to_markdown(self) -> str:
        lines = ["# GTFS-RT Snapshot Match Report", ""]
        if self.feed_timestamp:
            lines.append(f"**Feed timestamp:** {self.feed_timestamp}")
        rate = self.match_rate
        if rate is not None:
            lines.append(f"**Trip match rate:** {rate:.1%}")
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|----------|-------|")
        lines.append(f"| Trip updates matched | {self.trips_matched} |")
        lines.append(f"| Trip updates matched by fallback | {self.trips_matched_by_fallback} |")
        lines.append(f"| Trip updates ADDED (no static trip expected) | {self.trips_added} |")
        lines.append(f"| Trip updates CANCELED | {self.trips_canceled} |")
        lines.append(f"| Trip updates unmatched | {self.trips_unmatched} |")
        lines.append(f"| Vehicle positions matched | {self.vehicles_matched} |")
        lines.append(f"| Vehicle positions without trip | {self.vehicles_without_trip} |")
        lines.append(f"| Vehicle positions unmatched | {self.vehicles_unmatched} |")
        lines.append(f"| Alerts matched | {self.alerts_matched} |")
        lines.append(f"| Alerts unmatched | {self.alerts_unmatched} |")
        lines.append(f"| Unknown stop references | {self.unknown_stop_refs} |")
        lines.append("")

        ds = self.delay_summary()
        if ds:
            lines.append(f"**Delays:** avg {ds['avg_secs']:.0f}s, "
                         f"min {ds['min_secs']}s, max {ds['max_secs']}s "
                         f"({ds['count']} observations)")
            lines.append("")

        if self.issues:
            lines.append(f"## Issues ({len(self.issues)})")
            lines.append("")
            lines.append("| Category | Entity | ID | Message |")
            lines.append("|----------|--------|----|---------|")
            for i in self.issues[:50]:
                lines.append(f"| {i.category} | {i.entity_type} | `{i.entity_id}` | {i.message} |")
            if len(self.issues) > 50:
                lines.append(f"| ... | ... | ... | +{len(self.issues) - 50} more |")
            lines.append("")

        return "\n".join(lines)


def match_snapshot(snapshot: RtSnapshot, dataset: TransitDataset) -> RtMatchReport:
    """Join a real-time snapshot onto a static dataset.

    Resolution order for each trip reference:
      1. `trip_id` → `service_journey.id` (the normal case);
      2. fallback for feeds identifying trips by route + start time
         (common for frequency-based service — run `expand_frequencies()`
         first so each departure exists as a concrete journey):
         (`route_id`, `start_time`) → (`line_id`, `departure_time`).

    Trips flagged ADDED have no static counterpart by definition and are
    counted separately, never as unmatched. Duplicate-ID datasets make the
    join ambiguous — run `check_rt_readiness` first to catch that.
    """
    report = RtMatchReport(feed_timestamp=snapshot.feed_timestamp)

    journeys = dataset.db.get_records("service_journey")
    journey_ids = {r["id"] for r in journeys}
    stop_ids = {r["id"] for r in dataset.db.get_records("scheduled_stop_point")}
    line_ids = {r["id"] for r in dataset.db.get_records("line")}
    departures = {(r["line_id"], r["departure_time"]): r["id"] for r in journeys}

    def resolve_trip(trip: RtTripDescriptor) -> Optional[str]:
        if trip.trip_id and trip.trip_id in journey_ids:
            return trip.trip_id
        if trip.route_id and trip.start_time:
            return departures.get((trip.route_id, trip.start_time))
        return None

    for tu in snapshot.trip_updates:
        rel = tu.trip.schedule_relationship
        if rel == "ADDED":
            report.trips_added += 1
            continue
        if rel == "CANCELED":
            report.trips_canceled += 1

        journey_id = resolve_trip(tu.trip)
        if journey_id is None:
            report.trips_unmatched += 1
            report.issues.append(RtMatchIssue(
                "unmatched_trip", "trip_update", tu.trip.trip_id or tu.entity_id,
                "Trip reference not found in static dataset",
            ))
        else:
            if tu.trip.trip_id == journey_id:
                report.trips_matched += 1
            else:
                report.trips_matched_by_fallback += 1
            report.trip_resolution[tu.trip.trip_id or tu.entity_id] = journey_id
            if tu.delay is not None:
                report.delays.append(tu.delay)
            else:
                stu_delays = [s.arrival_delay for s in tu.stop_time_updates
                              if s.arrival_delay is not None]
                if stu_delays:
                    report.delays.append(stu_delays[-1])

        for stu in tu.stop_time_updates:
            if stu.stop_id and stu.stop_id not in stop_ids:
                report.unknown_stop_refs += 1
                report.issues.append(RtMatchIssue(
                    "unknown_stop", "stop_time_update", stu.stop_id,
                    f"Stop not found in static dataset (trip {tu.trip.trip_id or tu.entity_id})",
                ))

    for vp in snapshot.vehicle_positions:
        if vp.trip is None or (not vp.trip.trip_id and not vp.trip.route_id):
            report.vehicles_without_trip += 1
            continue
        if vp.trip.schedule_relationship == "ADDED":
            report.vehicles_without_trip += 1
            continue
        journey_id = resolve_trip(vp.trip)
        if journey_id is None:
            report.vehicles_unmatched += 1
            report.issues.append(RtMatchIssue(
                "unmatched_trip", "vehicle_position", vp.trip.trip_id or vp.entity_id,
                "Vehicle's trip reference not found in static dataset",
            ))
        else:
            report.vehicles_matched += 1
        if vp.stop_id and vp.stop_id not in stop_ids:
            report.unknown_stop_refs += 1
            report.issues.append(RtMatchIssue(
                "unknown_stop", "vehicle_position", vp.stop_id,
                f"Vehicle stop not found in static dataset (entity {vp.entity_id})",
            ))

    for alert in snapshot.alerts:
        refs_known = []
        for rid in alert.route_ids:
            refs_known.append(rid in line_ids)
        for sid in alert.stop_ids:
            refs_known.append(sid in stop_ids)
        for tid in alert.trip_ids:
            refs_known.append(tid in journey_ids)
        # An alert with no informed entities is network-wide: counts as matched.
        if not refs_known or any(refs_known):
            report.alerts_matched += 1
        else:
            report.alerts_unmatched += 1
            report.issues.append(RtMatchIssue(
                "unmatched_alert", "alert", alert.entity_id,
                "None of the alert's informed entities exist in the static dataset",
            ))

    return report


def snapshot_to_json(snapshot: RtSnapshot, indent: int = 2) -> str:
    """Serialize a snapshot to JSON (for inspection or downstream tooling)."""
    return json.dumps(snapshot.model_dump(), indent=indent)
