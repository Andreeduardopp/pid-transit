"""
Transmodel Validator.

Validates the logical consistency of the TransmodelDatabase data.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Set

from .database import TransmodelDatabase


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


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


def validate_transmodel(db: TransmodelDatabase) -> ValidationReport:
    """Run logical validation on the Transmodel data."""
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
        lat, lon = s.get("lat", 0), s.get("lon", 0)
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

    return report
