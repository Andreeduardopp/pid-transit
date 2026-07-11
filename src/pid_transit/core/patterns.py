"""
Journey-pattern equivalence (the JP/∼ quotient of docs/formalization.md §2).

Two journey patterns are equivalent iff they share the same line, direction, and
ordered stop sequence. The canonical representative of a class is the first
pattern encountered (matching the NeTEx exporter's historical behaviour), so the
same map drives both `NetexExporter(deduplicate_patterns=True)` and
`TransitDataset.normalize_patterns()`.
"""

from typing import Dict, List, Set, Tuple

from .database import TransmodelDatabase

# κ(jp) = (line_id, direction, ordered stop sequence)
PatternSignature = Tuple[object, object, Tuple[str, ...]]


def find_pattern_violations(db: TransmodelDatabase) -> Set[str]:
    """Journeys whose passing-time stop sequence disagrees with their pattern.

    This is the §4 V17 invariant — ``⟨stop(p) : p ∈ Π_j ordered⟩ = σ(jp)`` — and
    the precondition that makes §2 pattern deduplication safe: dedup groups by
    ``σ(jp)`` and assumes every journey actually follows its pattern, so a
    violating journey can be silently corrupted when its pattern is collapsed
    onto a canonical one with a different stop order.

    Returns the set of offending ``service_journey`` ids. A journey is only
    flagged when it has passing times *and* its pattern has ordered points (both
    sequences non-empty), so incomplete fixtures don't produce false positives.
    Shared by the validator and the NeTEx exporter's dedup guard.
    """
    points = db.get_records("point_in_journey_pattern")
    pattern_seq: Dict[str, List[str]] = {}
    for pt in sorted(points, key=lambda p: p["order"]):
        pattern_seq.setdefault(pt["journey_pattern_id"], []).append(pt["stop_point_id"])

    pt_by_journey: Dict[str, List[dict]] = {}
    for pt in db.get_records("passing_time"):
        pt_by_journey.setdefault(pt["service_journey_id"], []).append(pt)

    violations: Set[str] = set()
    for sj in db.get_records("service_journey"):
        expected = pattern_seq.get(sj.get("journey_pattern_id"))
        if not expected:
            continue
        actual = [p["stop_point_id"]
                  for p in sorted(pt_by_journey.get(sj["id"], []), key=lambda x: x["order"])]
        if actual and actual != expected:
            violations.add(sj["id"])
    return violations


def build_pattern_dedup_map(
    db: TransmodelDatabase,
) -> Tuple[Dict[str, str], Set[str]]:
    """Compute the §2 quotient over all journey patterns.

    Returns ``(remap, canonical_ids)`` where ``remap`` maps every pattern id to
    its class's canonical id and ``canonical_ids`` is the set of canonical ids.
    """
    patterns = db.get_records("journey_pattern")
    points = db.get_records("point_in_journey_pattern")

    points_by_pattern: Dict[str, List[dict]] = {}
    for pt in points:
        points_by_pattern.setdefault(pt["journey_pattern_id"], []).append(pt)

    sig_to_canonical: Dict[PatternSignature, str] = {}
    remap: Dict[str, str] = {}

    for jp in patterns:
        jp_id = jp["id"]
        pts = sorted(points_by_pattern.get(jp_id, []), key=lambda p: p["order"])
        stop_sig = tuple(p["stop_point_id"] for p in pts)
        sig = (jp.get("line_id"), jp.get("direction"), stop_sig)

        if sig not in sig_to_canonical:
            sig_to_canonical[sig] = jp_id
        remap[jp_id] = sig_to_canonical[sig]

    return remap, set(sig_to_canonical.values())
