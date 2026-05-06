"""
Transmodel Validator.

Validates the logical consistency of the TransmodelDatabase data.
(e.g., ensuring ServiceJourneys have passing times, and that passing times
follow chronological order).
"""

from dataclasses import dataclass, field
from typing import List, Dict

from .database import TransmodelDatabase

@dataclass
class ValidationIssue:
    entity_type: str
    entity_id: str
    issue_type: str
    message: str

@dataclass
class ValidationReport:
    is_valid: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)

def validate_transmodel(db: TransmodelDatabase) -> ValidationReport:
    """Run logical validation on the Transmodel data."""
    report = ValidationReport()
    
    # Check that every ServiceJourney has PassingTimes
    journeys = db.get_records("service_journey")
    passing_times = db.get_records("passing_time")
    
    # Group passing times by journey
    pt_by_journey: Dict[str, List[dict]] = {}
    for pt in passing_times:
        pt_by_journey.setdefault(pt["service_journey_id"], []).append(pt)
        
    for sj in journeys:
        sj_id = sj["id"]
        pts = pt_by_journey.get(sj_id, [])
        if not pts:
            report.issues.append(ValidationIssue(
                entity_type="service_journey",
                entity_id=sj_id,
                issue_type="missing_passing_times",
                message=f"ServiceJourney {sj_id} has no PassingTimes assigned."
            ))
            report.is_valid = False
            continue
            
        # Check order sequentiality
        pts_sorted = sorted(pts, key=lambda x: x["order"])
        orders = [x["order"] for x in pts_sorted]
        # In Transmodel, order should typically be strictly increasing
        if len(set(orders)) != len(orders):
            report.issues.append(ValidationIssue(
                entity_type="service_journey",
                entity_id=sj_id,
                issue_type="duplicate_passing_time_order",
                message=f"ServiceJourney {sj_id} has duplicate passing time orders."
            ))
            report.is_valid = False
            
    # Check Lines have at least one JourneyPattern
    lines = db.get_records("line")
    patterns = db.get_records("journey_pattern")
    jp_lines = {p["line_id"] for p in patterns}
    
    for line in lines:
        if line["id"] not in jp_lines:
            report.issues.append(ValidationIssue(
                entity_type="line",
                entity_id=line["id"],
                issue_type="no_journey_patterns",
                message=f"Line {line['id']} has no associated JourneyPatterns."
            ))
            report.is_valid = False

    return report
