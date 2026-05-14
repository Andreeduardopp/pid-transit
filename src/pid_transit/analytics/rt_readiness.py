"""
GTFS-RT readiness checker.

Validates that a transit dataset has the characteristics needed for a
reliable GTFS-Realtime feed: unique and well-formed IDs, consistent
stop sequences, and no ambiguous references.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

from ..core.dataset import TransitDataset

_URL_UNSAFE = re.compile(r'[^A-Za-z0-9_\-.:/@]')


@dataclass
class RTIssue:
    category: str
    entity_type: str
    entity_id: str
    message: str


@dataclass
class RTReadinessReport:
    is_ready: bool = True
    issues: List[RTIssue] = field(default_factory=list)
    checks_passed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "is_ready": self.is_ready,
            "checks_passed": self.checks_passed,
            "issue_count": len(self.issues),
            "issues": [
                {"category": i.category, "entity_type": i.entity_type,
                 "entity_id": i.entity_id, "message": i.message}
                for i in self.issues
            ],
        }

    def to_markdown(self) -> str:
        lines = ["# GTFS-RT Readiness Report", ""]
        status = "READY" if self.is_ready else "NOT READY"
        lines.append(f"**Status:** {status}")
        lines.append("")

        if self.checks_passed:
            lines.append("## Checks Passed")
            for c in self.checks_passed:
                lines.append(f"- {c}")
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


def check_rt_readiness(dataset: TransitDataset) -> RTReadinessReport:
    """Run all GTFS-RT readiness checks on the dataset."""
    report = RTReadinessReport()

    _check_id_uniqueness(dataset, report)
    _check_id_format(dataset, report)
    _check_stop_sequence_consistency(dataset, report)
    _check_non_empty_ids(dataset, report)

    return report


def _check_id_uniqueness(dataset: TransitDataset, report: RTReadinessReport) -> None:
    tables = {
        "service_journey": "trip_id",
        "scheduled_stop_point": "stop_id",
        "line": "route_id",
    }
    all_unique = True
    for table, gtfs_name in tables.items():
        records = dataset.db.get_records(table)
        ids = [r["id"] for r in records]
        dupes = len(ids) - len(set(ids))
        if dupes:
            all_unique = False
            report.is_ready = False
            seen: Dict[str, int] = {}
            for eid in ids:
                seen[eid] = seen.get(eid, 0) + 1
            for eid, count in seen.items():
                if count > 1:
                    report.issues.append(RTIssue(
                        "duplicate_id", table, eid,
                        f"Duplicate {gtfs_name}: appears {count} times",
                    ))
    if all_unique:
        report.checks_passed.append("All trip, stop, and route IDs are unique")


def _check_id_format(dataset: TransitDataset, report: RTReadinessReport) -> None:
    tables = ["service_journey", "scheduled_stop_point", "line"]
    all_clean = True
    for table in tables:
        records = dataset.db.get_records(table)
        for r in records:
            eid = r["id"]
            if _URL_UNSAFE.search(eid):
                all_clean = False
                report.is_ready = False
                report.issues.append(RTIssue(
                    "unsafe_id", table, eid,
                    "ID contains URL-unsafe or whitespace characters",
                ))
    if all_clean:
        report.checks_passed.append("All IDs are URL-safe (no whitespace or special characters)")


def _check_stop_sequence_consistency(dataset: TransitDataset, report: RTReadinessReport) -> None:
    patterns = dataset.db.get_records("journey_pattern")
    points = dataset.db.get_records("point_in_journey_pattern")

    pts_by_pattern: Dict[str, List[dict]] = {}
    for p in points:
        pts_by_pattern.setdefault(p["journey_pattern_id"], []).append(p)

    all_consistent = True
    for jp in patterns:
        jp_id = jp["id"]
        pts = pts_by_pattern.get(jp_id, [])
        if not pts:
            continue
        orders = sorted(p["order"] for p in pts)
        if len(orders) != len(set(orders)):
            all_consistent = False
            report.is_ready = False
            report.issues.append(RTIssue(
                "inconsistent_sequence", "journey_pattern", jp_id,
                "Duplicate stop sequence orders in journey pattern",
            ))

    if all_consistent:
        report.checks_passed.append("All journey patterns have consistent stop sequences")


def _check_non_empty_ids(dataset: TransitDataset, report: RTReadinessReport) -> None:
    tables = ["service_journey", "scheduled_stop_point", "line"]
    all_non_empty = True
    for table in tables:
        records = dataset.db.get_records(table)
        for r in records:
            if not r["id"] or not r["id"].strip():
                all_non_empty = False
                report.is_ready = False
                report.issues.append(RTIssue(
                    "empty_id", table, "(empty)",
                    "Entity has an empty or whitespace-only ID",
                ))
    if all_non_empty:
        report.checks_passed.append("No empty or whitespace-only IDs found")
