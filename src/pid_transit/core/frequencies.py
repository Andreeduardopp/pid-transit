"""Frequency expansion and compression (roadmap task 8.4).

A frequency-based feed represents dozens of departures with a single *template*
journey plus a ``Frequency`` window (``start``, ``end``, ``headway``). That is
compact but makes every docs/formalization.md §3 metric wrong: one template
journey undercounts vehicle-hours and misreports headways.

This module is the bidirectional bridge between the two representations, both as
in-place mutations on a :class:`TransmodelDatabase`:

* :func:`expand_frequencies` — materialize each window into concrete journeys at
  ``start, start+h, … < end`` (GTFS end is exclusive), shifting the template's
  passing times by each departure's offset. After this, §3 metrics are exact.
* :func:`compress_to_frequencies` — the inverse: within a
  ``(journey_pattern, day_type, timing-profile θ)`` class, find maximal
  arithmetic departure runs and replace each with one template + a ``Frequency``.

The timing profile θ(j) = ⟨τ(t_k) − τ(t_1)⟩ (the offsets from the first stop's
departure) is exactly what makes two journeys "the same trip at a different
start time". Because a compressed run shares one θ, ``expand(compress(X))``
reproduces X's departures *and* passing times exactly (the round-trip property
of task 2, guaranteed for ``exact_times = 1``).
"""

from typing import Dict, List, Optional

from .database import TransmodelDatabase


def _tau(t: Optional[str]) -> Optional[int]:
    """GTFS time string → seconds since service-day start (HH may be ≥ 24)."""
    if not t:
        return None
    try:
        parts = t.strip().split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        return None


def _fmt(s: int) -> str:
    """Seconds → HH:MM:SS, keeping HH ≥ 24 for overnight service."""
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _profile(pts: List[dict]) -> Optional[tuple]:
    """θ(j): per-stop (order, arrival offset, departure offset) from the first
    stop's departure. Returns None if the journey has no usable base time."""
    base = _tau(pts[0].get("departure_time") or pts[0].get("arrival_time"))
    if base is None:
        return None
    prof = []
    for p in pts:
        arr, dep = _tau(p.get("arrival_time")), _tau(p.get("departure_time"))
        prof.append((
            p["order"],
            None if arr is None else arr - base,
            None if dep is None else dep - base,
        ))
    return tuple(prof)


def expand_frequencies(db: TransmodelDatabase) -> Dict[str, int]:
    """Materialize every ``Frequency`` window into concrete service journeys.

    Each template journey referenced by a frequency is replaced by one journey
    per departure in ``[start, end)`` stepping by ``headway_secs``; passing times
    are shifted by the departure offset. The template journey, its passing times,
    and the frequency rows are removed.

    Returns counts ``{"frequencies_expanded", "journeys_created",
    "passing_times_created"}``.
    """
    freqs = db.get_records("frequency")
    if not freqs:
        return {"frequencies_expanded": 0, "journeys_created": 0,
                "passing_times_created": 0}

    new_journeys: List[dict] = []
    new_pts: List[dict] = []
    templates: set = set()
    expanded = 0

    for f in freqs:
        jid = f["service_journey_id"]
        start, end, h = _tau(f["start_time"]), _tau(f["end_time"]), f["headway_secs"]
        if start is None or end is None or not h or h <= 0 or start >= end:
            continue
        sj = db.get_one("service_journey", {"id": jid})
        pts = db.query("passing_time", where={"service_journey_id": jid},
                       order_by="order")
        if not sj or not pts:
            continue
        base = _tau(pts[0].get("departure_time") or pts[0].get("arrival_time"))
        if base is None:
            continue

        instances = list(range(start, end, h))
        for i, dep0 in enumerate(instances):
            new_id = f"{jid}#{i}"
            nj = dict(sj)
            nj["id"] = new_id
            nj["departure_time"] = _fmt(dep0)
            new_journeys.append(nj)
            for p in pts:
                np = dict(p)
                np["service_journey_id"] = new_id
                arr, dep = _tau(p.get("arrival_time")), _tau(p.get("departure_time"))
                if arr is not None:
                    np["arrival_time"] = _fmt(dep0 + arr - base)
                if dep is not None:
                    np["departure_time"] = _fmt(dep0 + dep - base)
                new_pts.append(np)
        templates.add(jid)
        expanded += 1

    # Drop templates (passing times first: FK), then their frequency rows, then
    # insert the concrete journeys (before their passing times, again for FK).
    for jid in templates:
        db.delete("passing_time", {"service_journey_id": jid})
        db.delete("frequency", {"service_journey_id": jid})
        db.delete("service_journey", {"id": jid})
    if new_journeys:
        db.upsert("service_journey", new_journeys)
    if new_pts:
        db.upsert("passing_time", new_pts)

    return {
        "frequencies_expanded": expanded,
        "journeys_created": len(new_journeys),
        "passing_times_created": len(new_pts),
    }


def compress_to_frequencies(
    db: TransmodelDatabase, min_run: int = 3
) -> Dict[str, int]:
    """Detect regular departure progressions and replace them with frequencies.

    Journeys are grouped by ``(journey_pattern_id, day_type_id, θ)``; within each
    group the sorted departures are scanned for maximal arithmetic runs (constant
    headway). A run of at least ``min_run`` departures is collapsed to its first
    journey (the template) plus a ``Frequency`` row spanning ``[d₀, d_last + h)``
    with ``exact_times = 1``; the other journeys in the run are deleted.

    Returns counts ``{"runs_compressed", "journeys_removed", "frequencies_created"}``.
    """
    if min_run < 2:
        raise ValueError("min_run must be at least 2")

    pts_by_j: Dict[str, List[dict]] = {}
    for p in db.get_records("passing_time"):
        pts_by_j.setdefault(p["service_journey_id"], []).append(p)

    # key -> sorted list of (departure_seconds, journey_id)
    groups: Dict[tuple, List[tuple]] = {}
    for sj in db.get_records("service_journey"):
        pts = sorted(pts_by_j.get(sj["id"], []), key=lambda p: p["order"])
        if not pts:
            continue
        theta = _profile(pts)
        base = _tau(pts[0].get("departure_time") or pts[0].get("arrival_time"))
        if theta is None or base is None:
            continue
        key = (sj.get("journey_pattern_id"), sj.get("day_type_id"), theta)
        groups.setdefault(key, []).append((base, sj["id"]))

    runs_compressed = journeys_removed = frequencies_created = 0

    for members in groups.values():
        members.sort(key=lambda m: m[0])
        n = len(members)
        i = 0
        while i + 1 < n:
            h = members[i + 1][0] - members[i][0]
            if h <= 0:  # duplicate/backwards departures can't form a headway
                i += 1
                continue
            j = i + 1
            while j + 1 < n and members[j + 1][0] - members[j][0] == h:
                j += 1
            run = members[i:j + 1]
            if len(run) >= min_run:
                template_id = run[0][1]
                db.upsert("frequency", [{
                    "service_journey_id": template_id,
                    "start_time": _fmt(run[0][0]),
                    "end_time": _fmt(run[-1][0] + h),  # exclusive → regenerates d_last
                    "headway_secs": h,
                    "exact_times": 1,
                }])
                frequencies_created += 1
                runs_compressed += 1
                for _, jid in run[1:]:
                    db.delete("passing_time", {"service_journey_id": jid})
                    db.delete("service_journey", {"id": jid})
                    journeys_removed += 1
                i = j + 1
            else:
                i += 1

    return {
        "runs_compressed": runs_compressed,
        "journeys_removed": journeys_removed,
        "frequencies_created": frequencies_created,
    }
