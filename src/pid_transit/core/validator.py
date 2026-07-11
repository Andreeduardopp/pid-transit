"""
Transmodel Validator.

Validates the logical consistency of the TransmodelDatabase data.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Set

from .database import TransmodelDatabase
from .geo import StopGrid, haversine
from .schemas import TransportMode


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# Ordering for --min-severity filtering (higher = more severe).
_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}

# V16: hard per-mode implied-speed ceilings (km/h). Above these a pair of
# consecutive passing times is physically implausible and flagged as an error.
_SPEED_CEILING_KMH = {
    TransportMode.BUS: 150,
    TransportMode.TROLLEYBUS: 120,
    TransportMode.TRAM: 120,
    TransportMode.METRO: 150,
    TransportMode.RAIL: 350,
    TransportMode.MONORAIL: 150,
    TransportMode.FERRY: 100,
    TransportMode.CABLE_CAR: 60,
    TransportMode.GONDOLA: 60,
    TransportMode.FUNICULAR: 60,
}
_SPEED_CEILING_DEFAULT_KMH = 300
# Below this separation, coordinate noise dominates, so V16 skips the pair.
_MIN_SPEED_DISTANCE_M = 50.0


@dataclass
class ValidationIssue:
    entity_type: str
    entity_id: str
    issue_type: str
    message: str
    severity: Severity = Severity.ERROR


@dataclass
class ValidationReport:
    is_valid: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)

    def _append(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.severity == Severity.ERROR:
            self.is_valid = False


_TIME_RE = re.compile(r"^\d{2,3}:\d{2}:\d{2}$")


def _time_to_secs(t: str) -> int:
    parts = t.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def validate_transmodel(
    db: TransmodelDatabase, min_severity: Severity = Severity.INFO
) -> ValidationReport:
    """Run logical validation on the Transmodel data.

    Rules V01–V15 cover structural integrity; V16–V20 add *semantic* checks
    (formalization.md §4) — data that is structurally valid yet operationally
    impossible. ``min_severity`` filters the returned issues (errors are always
    kept); ``is_valid`` reflects errors regardless of the filter.
    """
    report = ValidationReport()

    journeys = db.get_records("service_journey")
    passing_times = db.get_records("passing_time")
    lines = db.get_records("line")
    patterns = db.get_records("journey_pattern")
    stops = db.get_records("scheduled_stop_point")
    day_types = db.get_records("day_type")
    exceptions = db.get_records("operating_day_exception")
    frequencies = db.get_records("frequency")

    stop_ids: Set[str] = {s["id"] for s in stops}
    day_type_ids: Set[str] = {dt["id"] for dt in day_types}
    journey_ids: Set[str] = {sj["id"] for sj in journeys}
    jp_ids: Set[str] = {p["id"] for p in patterns}
    jp_line_ids: Set[str] = {p["line_id"] for p in patterns}
    sj_line_ids: Set[str] = {sj["line_id"] for sj in journeys}

    pt_by_journey: Dict[str, List[dict]] = {}
    for pt in passing_times:
        pt_by_journey.setdefault(pt["service_journey_id"], []).append(pt)

    # --- V01: Every ServiceJourney has at least one PassingTime ---
    for sj in journeys:
        sj_id = sj["id"]
        pts = pt_by_journey.get(sj_id, [])
        if not pts:
            report._append(ValidationIssue(
                entity_type="service_journey",
                entity_id=sj_id,
                issue_type="missing_passing_times",
                message=f"ServiceJourney {sj_id} has no PassingTimes assigned.",
            ))
            continue

        # --- V02: No duplicate orders within a journey ---
        orders = [x["order"] for x in pts]
        if len(set(orders)) != len(orders):
            report._append(ValidationIssue(
                entity_type="service_journey",
                entity_id=sj_id,
                issue_type="duplicate_passing_time_order",
                message=f"ServiceJourney {sj_id} has duplicate passing time orders.",
            ))

        # --- V04: Arrival times non-decreasing along journey ---
        pts_sorted = sorted(pts, key=lambda x: x["order"])
        prev_secs = -1
        for pt in pts_sorted:
            arr = pt.get("arrival_time") or pt.get("departure_time")
            if arr and _TIME_RE.match(arr):
                secs = _time_to_secs(arr)
                if secs < prev_secs:
                    report._append(ValidationIssue(
                        entity_type="passing_time",
                        entity_id=f"{sj_id}@order={pt['order']}",
                        issue_type="time_decreases",
                        message=f"Time decreases at order {pt['order']} in journey {sj_id}.",
                    ))
                    break
                prev_secs = secs

    # --- V03: Every Line has at least one JourneyPattern ---
    for line in lines:
        if line["id"] not in jp_line_ids:
            report._append(ValidationIssue(
                entity_type="line",
                entity_id=line["id"],
                issue_type="no_journey_patterns",
                message=f"Line {line['id']} has no associated JourneyPatterns.",
            ))

    # --- V05: PassingTime stop_point_id references existing stop ---
    for pt in passing_times:
        if pt["stop_point_id"] not in stop_ids:
            report._append(ValidationIssue(
                entity_type="passing_time",
                entity_id=f"{pt['service_journey_id']}@{pt['stop_point_id']}",
                issue_type="orphan_stop_reference",
                message=f"PassingTime references non-existent stop {pt['stop_point_id']}.",
            ))

    # --- V06: ServiceJourney day_type_id references existing DayType ---
    for sj in journeys:
        if sj["day_type_id"] not in day_type_ids:
            report._append(ValidationIssue(
                entity_type="service_journey",
                entity_id=sj["id"],
                issue_type="orphan_day_type",
                message=f"ServiceJourney {sj['id']} references non-existent DayType {sj['day_type_id']}.",
            ))

    # --- V07: DayType start_date <= end_date ---
    for dt in day_types:
        if dt["start_date"] > dt["end_date"]:
            report._append(ValidationIssue(
                entity_type="day_type",
                entity_id=dt["id"],
                issue_type="invalid_date_range",
                message=f"DayType {dt['id']} has start_date > end_date.",
            ))

    # --- V08: OperatingDayException references existing DayType ---
    for exc in exceptions:
        if exc["day_type_id"] not in day_type_ids:
            report._append(ValidationIssue(
                entity_type="operating_day_exception",
                entity_id=f"{exc['day_type_id']}@{exc['date']}",
                issue_type="orphan_day_type",
                message=f"Exception references non-existent DayType {exc['day_type_id']}.",
            ))

    # --- V09: ServiceJourney departure_time format ---
    for sj in journeys:
        dep = sj.get("departure_time", "")
        if dep and not _TIME_RE.match(dep):
            report._append(ValidationIssue(
                entity_type="service_journey",
                entity_id=sj["id"],
                issue_type="invalid_time_format",
                message=f"ServiceJourney {sj['id']} has invalid departure_time '{dep}'.",
                severity=Severity.WARNING,
            ))

    # --- V10: Stop coordinates plausibility ---
    for s in stops:
        # lat/lon are nullable since schema 3.1; treat missing as (0,0) so the
        # null-island warning fires instead of abs(None) crashing below.
        lat = s.get("lat") or 0
        lon = s.get("lon") or 0
        if lat == 0 and lon == 0:
            report._append(ValidationIssue(
                entity_type="scheduled_stop_point",
                entity_id=s["id"],
                issue_type="null_island_coordinates",
                message=f"Stop {s['id']} is at (0,0) — likely missing coordinates.",
                severity=Severity.WARNING,
            ))
        elif abs(lat) > 90 or abs(lon) > 180:
            report._append(ValidationIssue(
                entity_type="scheduled_stop_point",
                entity_id=s["id"],
                issue_type="out_of_range_coordinates",
                message=f"Stop {s['id']} has out-of-range coordinates ({lat}, {lon}).",
                severity=Severity.WARNING,
            ))

    # --- V11: Line has at least one ServiceJourney ---
    for line in lines:
        if line["id"] not in sj_line_ids:
            report._append(ValidationIssue(
                entity_type="line",
                entity_id=line["id"],
                issue_type="no_service_journeys",
                message=f"Line {line['id']} has no ServiceJourneys.",
                severity=Severity.WARNING,
            ))

    # --- V12: Feed date coverage ---
    feed_infos = db.get_records("feed_info")
    for fi in feed_infos:
        fi_start = fi.get("start_date")
        fi_end = fi.get("end_date")
        if fi_start and fi_end and day_types:
            has_overlap = any(
                dt["start_date"] <= fi_end and dt["end_date"] >= fi_start
                for dt in day_types
            )
            if not has_overlap:
                report._append(ValidationIssue(
                    entity_type="feed_info",
                    entity_id=fi.get("id", "default_feed"),
                    issue_type="no_date_overlap",
                    message="No DayType validity period overlaps with feed date range.",
                    severity=Severity.INFO,
                ))

    # --- V13: Pathway references valid stops/stop_areas ---
    try:
        pathway_records = db.get_records("pathway")
        stop_area_ids: Set[str] = {sa["id"] for sa in db.get_records("stop_area")}
        all_stop_ids = stop_ids | stop_area_ids
        for pw in pathway_records:
            for field_name in ("from_stop_id", "to_stop_id"):
                if pw[field_name] not in all_stop_ids:
                    report._append(ValidationIssue(
                        entity_type="pathway",
                        entity_id=pw["id"],
                        issue_type="orphan_stop_reference",
                        message=f"Pathway {pw['id']} references non-existent stop {pw[field_name]}.",
                    ))
    except Exception:
        pass

    # --- V14: Frequency references valid ServiceJourney ---
    for fr in frequencies:
        if fr["service_journey_id"] not in journey_ids:
            report._append(ValidationIssue(
                entity_type="frequency",
                entity_id=f"{fr['service_journey_id']}@{fr['start_time']}",
                issue_type="orphan_journey_reference",
                message=f"Frequency references non-existent ServiceJourney {fr['service_journey_id']}.",
            ))

    # --- V15: ServiceJourney journey_pattern_id references existing JourneyPattern ---
    for sj in journeys:
        jp_id = sj.get("journey_pattern_id")
        if jp_id and jp_id not in jp_ids:
            report._append(ValidationIssue(
                entity_type="service_journey",
                entity_id=sj["id"],
                issue_type="orphan_pattern_reference",
                message=f"ServiceJourney {sj['id']} references non-existent JourneyPattern {jp_id}.",
            ))

    # Shared lookups for the semantic rules below.
    stop_coords: Dict[str, tuple] = {
        s["id"]: (s.get("lat"), s.get("lon")) for s in stops
    }
    line_mode: Dict[str, str] = {ln["id"]: ln.get("transport_mode") for ln in lines}
    pijp = db.get_records("point_in_journey_pattern")

    # --- V16: Travel-speed plausibility (§3.5 Haversine over consecutive stops) ---
    # Journeys share patterns, so the same consecutive stop pair recurs across
    # thousands of journeys; memoize its distance instead of re-deriving it.
    pair_dist: Dict[tuple, Optional[float]] = {}
    for sj in journeys:
        pts = sorted(pt_by_journey.get(sj["id"], []), key=lambda x: x["order"])
        ceiling = _SPEED_CEILING_KMH.get(line_mode.get(sj["line_id"]),
                                         _SPEED_CEILING_DEFAULT_KMH)
        for a, b in zip(pts, pts[1:]):
            pair = (a["stop_point_id"], b["stop_point_id"])
            dist = pair_dist.get(pair, -1.0)
            if dist == -1.0:
                la, loa = stop_coords.get(pair[0], (None, None))
                lb, lob = stop_coords.get(pair[1], (None, None))
                # None marks a pair unusable (schema 3.1 allows NULL coordinates)
                dist = (None if None in (la, loa, lb, lob)
                        else haversine(la, loa, lb, lob))
                pair_dist[pair] = dist
            if dist is None:
                continue
            if dist < _MIN_SPEED_DISTANCE_M:
                continue
            t1 = a.get("departure_time") or a.get("arrival_time")
            t2 = b.get("arrival_time") or b.get("departure_time")
            if not (t1 and _TIME_RE.match(t1) and t2 and _TIME_RE.match(t2)):
                continue
            dt = _time_to_secs(t2) - _time_to_secs(t1)
            if dt <= 0:
                report._append(ValidationIssue(
                    entity_type="service_journey",
                    entity_id=f"{sj['id']}@order={b['order']}",
                    issue_type="implausible_speed",
                    message=(f"Journey {sj['id']} covers {dist:.0f} m between stops "
                             f"{a['stop_point_id']} and {b['stop_point_id']} in "
                             f"{dt} s (zero/negative time)."),
                    severity=Severity.WARNING,
                ))
                continue
            speed_kmh = dist / dt * 3.6
            if speed_kmh > ceiling:
                report._append(ValidationIssue(
                    entity_type="service_journey",
                    entity_id=f"{sj['id']}@order={b['order']}",
                    issue_type="implausible_speed",
                    message=(f"Journey {sj['id']} implies {speed_kmh:.0f} km/h between "
                             f"stops {a['stop_point_id']} and {b['stop_point_id']} "
                             f"(ceiling {ceiling} km/h)."),
                ))

    # --- V17: Pattern / passing-time consistency ---
    # The invariant that makes §2 pattern dedup safe: a journey's stop sequence
    # must equal its journey pattern's ordered points. Shares one implementation
    # with the NeTEx exporter's dedup guard (core.patterns.find_pattern_violations).
    from .patterns import find_pattern_violations
    violations = find_pattern_violations(db)
    for sj in journeys:
        if sj["id"] in violations:
            report._append(ValidationIssue(
                entity_type="service_journey",
                entity_id=sj["id"],
                issue_type="pattern_sequence_mismatch",
                message=(f"Journey {sj['id']} stop sequence does not match its "
                         f"JourneyPattern {sj.get('journey_pattern_id')}."),
            ))

    # --- V18: Duplicate-stop detection (same normalized name within 10 m) ---
    named: Dict[str, List[dict]] = {}
    for s in stops:
        if s.get("lat") is None or s.get("lon") is None:
            continue
        named.setdefault((s.get("name") or "").strip().lower(), []).append(s)
    for group in named.values():
        # StopGrid keeps a group of k same-named stops at O(k·neighbors)
        # instead of the k²/2 all-pairs scan (degenerate when a feed has many
        # unnamed stops: they all share the "" key).
        grid = StopGrid(((s["id"], s["lat"], s["lon"]) for s in group), cell_m=10.0)
        pos = {s["id"]: i for i, s in enumerate(group)}
        for s in group:
            for other_id, d in grid.within(s["lat"], s["lon"], 10.0):
                if pos[other_id] >= pos[s["id"]]:
                    continue  # report each pair once, later stop as duplicate
                report._append(ValidationIssue(
                    entity_type="scheduled_stop_point",
                    entity_id=s["id"],
                    issue_type="duplicate_stop",
                    message=(f"Stop {s['id']} duplicates {other_id} "
                             f"(same name within {d:.0f} m)."),
                    severity=Severity.WARNING,
                ))

    # --- V19: Unused entities (orphan inventory) ---
    referenced_stops = {pt["stop_point_id"] for pt in passing_times}
    referenced_stops |= {p["stop_point_id"] for p in pijp}
    for s in stops:
        if s["id"] not in referenced_stops:
            report._append(ValidationIssue(
                entity_type="scheduled_stop_point",
                entity_id=s["id"],
                issue_type="unused_stop",
                message=f"Stop {s['id']} is never used by any journey or pattern.",
                severity=Severity.INFO,
            ))
    used_patterns = {sj.get("journey_pattern_id") for sj in journeys}
    for p in patterns:
        if p["id"] not in used_patterns:
            report._append(ValidationIssue(
                entity_type="journey_pattern",
                entity_id=p["id"],
                issue_type="unused_pattern",
                message=f"JourneyPattern {p['id']} has no ServiceJourneys.",
                severity=Severity.INFO,
            ))
    used_day_types = {sj["day_type_id"] for sj in journeys}
    used_day_types |= {exc["day_type_id"] for exc in exceptions}
    for dt in day_types:
        if dt["id"] not in used_day_types:
            report._append(ValidationIssue(
                entity_type="day_type",
                entity_id=dt["id"],
                issue_type="unused_day_type",
                message=f"DayType {dt['id']} has no journeys and no exceptions.",
                severity=Severity.INFO,
            ))
    referenced_shapes = {sj.get("shape_id") for sj in journeys if sj.get("shape_id")}
    shape_ids = {sp["shape_id"] for sp in db.get_records("shape_point")}
    for shape_id in shape_ids - referenced_shapes:
        report._append(ValidationIssue(
            entity_type="shape_point",
            entity_id=shape_id,
            issue_type="unused_shape",
            message=f"Shape {shape_id} is never referenced by a ServiceJourney.",
            severity=Severity.INFO,
        ))

    # --- V20: Frequency sanity ---
    freq_by_journey: Dict[str, List[dict]] = {}
    for fr in frequencies:
        start, end = fr.get("start_time"), fr.get("end_time")
        if start and end and _TIME_RE.match(start) and _TIME_RE.match(end):
            if _time_to_secs(start) >= _time_to_secs(end):
                report._append(ValidationIssue(
                    entity_type="frequency",
                    entity_id=f"{fr['service_journey_id']}@{start}",
                    issue_type="frequency_window",
                    message=f"Frequency window {start}-{end} is empty or reversed.",
                    severity=Severity.WARNING,
                ))
            freq_by_journey.setdefault(fr["service_journey_id"], []).append(fr)
        if fr.get("headway_secs") is not None and fr["headway_secs"] <= 0:
            report._append(ValidationIssue(
                entity_type="frequency",
                entity_id=f"{fr['service_journey_id']}@{fr.get('start_time')}",
                issue_type="frequency_headway",
                message=f"Frequency has non-positive headway {fr['headway_secs']}s.",
                severity=Severity.WARNING,
            ))
    for sj_id, frs in freq_by_journey.items():
        ordered = sorted(frs, key=lambda f: _time_to_secs(f["start_time"]))
        for prev, cur in zip(ordered, ordered[1:]):
            if _time_to_secs(cur["start_time"]) < _time_to_secs(prev["end_time"]):
                report._append(ValidationIssue(
                    entity_type="frequency",
                    entity_id=f"{sj_id}@{cur['start_time']}",
                    issue_type="frequency_overlap",
                    message=(f"Frequency windows overlap for journey {sj_id} "
                             f"({prev['start_time']}-{prev['end_time']} and "
                             f"{cur['start_time']}-{cur['end_time']})."),
                    severity=Severity.WARNING,
                ))

    if min_severity != Severity.INFO:
        threshold = _SEVERITY_RANK[min_severity]
        report.issues = [
            i for i in report.issues if _SEVERITY_RANK[i.severity] >= threshold
        ]

    return report
