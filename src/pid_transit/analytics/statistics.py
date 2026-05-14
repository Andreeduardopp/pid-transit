"""
Operational statistics for transit datasets.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.dataset import TransitDataset


def _time_to_seconds(t: str) -> int:
    parts = t.strip().split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def _seconds_to_time(s: int) -> str:
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


class TransitStatistics:
    """Computes operational statistics from a TransitDataset."""

    def __init__(
        self,
        dataset: TransitDataset,
        peak_hours: Optional[List[Tuple[str, str]]] = None,
    ):
        self.dataset = dataset
        self.peak_hours = peak_hours or [("07:00:00", "09:00:00"), ("17:00:00", "19:00:00")]

    def _is_peak(self, time_str: str) -> bool:
        t = _time_to_seconds(time_str)
        for start, end in self.peak_hours:
            if _time_to_seconds(start) <= t < _time_to_seconds(end):
                return True
        return False

    def service_span(
        self, line_id: Optional[str] = None
    ) -> Dict[str, Dict[str, Dict[str, str]]]:
        """First and last departure per line per day type.

        Returns: {line_id: {day_type_id: {"first": HH:MM:SS, "last": HH:MM:SS}}}
        """
        journeys = self.dataset.service_journeys.get_all()
        if line_id:
            journeys = [j for j in journeys if j.line_id == line_id]

        spans: Dict[str, Dict[str, Dict[str, str]]] = {}
        for sj in journeys:
            lid = sj.line_id
            dtid = sj.day_type_id
            t = _time_to_seconds(sj.departure_time)
            if lid not in spans:
                spans[lid] = {}
            if dtid not in spans[lid]:
                spans[lid][dtid] = {"first_s": t, "last_s": t}
            else:
                entry = spans[lid][dtid]
                if t < entry["first_s"]:
                    entry["first_s"] = t
                if t > entry["last_s"]:
                    entry["last_s"] = t

        result: Dict[str, Dict[str, Dict[str, str]]] = {}
        for lid, dt_map in spans.items():
            result[lid] = {}
            for dtid, entry in dt_map.items():
                result[lid][dtid] = {
                    "first": _seconds_to_time(entry["first_s"]),
                    "last": _seconds_to_time(entry["last_s"]),
                }
        return result

    def headways(
        self, line_id: str, day_type_id: str
    ) -> Dict[str, Optional[float]]:
        """Compute headway statistics for a line on a given day type.

        Returns: {"avg", "min", "max", "peak_avg", "offpeak_avg"} in seconds.
        """
        journeys = [
            sj for sj in self.dataset.service_journeys.get_all()
            if sj.line_id == line_id and sj.day_type_id == day_type_id
        ]
        if len(journeys) < 2:
            return {"avg": None, "min": None, "max": None,
                    "peak_avg": None, "offpeak_avg": None}

        times = sorted(_time_to_seconds(sj.departure_time) for sj in journeys)
        gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]

        peak_gaps = []
        offpeak_gaps = []
        for i in range(len(times) - 1):
            mid = (times[i] + times[i + 1]) // 2
            g = times[i + 1] - times[i]
            if self._is_peak(_seconds_to_time(mid)):
                peak_gaps.append(g)
            else:
                offpeak_gaps.append(g)

        return {
            "avg": sum(gaps) / len(gaps),
            "min": min(gaps),
            "max": max(gaps),
            "peak_avg": sum(peak_gaps) / len(peak_gaps) if peak_gaps else None,
            "offpeak_avg": sum(offpeak_gaps) / len(offpeak_gaps) if offpeak_gaps else None,
        }

    def vehicle_hours(self, day_type_id: Optional[str] = None) -> float:
        """Total vehicle-hours of service (sum of first-to-last passing time per journey)."""
        journeys = self.dataset.service_journeys.get_all()
        if day_type_id:
            journeys = [j for j in journeys if j.day_type_id == day_type_id]

        total_seconds = 0.0
        for sj in journeys:
            pts = self.dataset.passing_times.get_by_journey(sj.id)
            if len(pts) < 2:
                continue
            times = []
            for pt in pts:
                t = pt.departure_time or pt.arrival_time
                if t:
                    times.append(_time_to_seconds(t))
            if len(times) >= 2:
                total_seconds += max(times) - min(times)

        return total_seconds / 3600.0

    def stop_coverage(
        self, day_type_id: Optional[str] = None
    ) -> Dict[str, int]:
        """Count departures per stop.

        Returns: {stop_id: departure_count}
        """
        journeys = self.dataset.service_journeys.get_all()
        if day_type_id:
            journey_ids = {j.id for j in journeys if j.day_type_id == day_type_id}
        else:
            journey_ids = {j.id for j in journeys}

        coverage: Dict[str, int] = {}
        all_pts = self.dataset.passing_times.get_all()
        for pt in all_pts:
            if pt.service_journey_id in journey_ids and pt.departure_time:
                coverage[pt.stop_point_id] = coverage.get(pt.stop_point_id, 0) + 1
        return coverage

    def service_balance(self) -> Dict[str, Dict[str, int]]:
        """Journeys and lines per day type.

        Returns: {day_type_id: {"journey_count": N, "line_count": N}}
        """
        journeys = self.dataset.service_journeys.get_all()
        balance: Dict[str, Dict[str, set]] = {}
        for sj in journeys:
            if sj.day_type_id not in balance:
                balance[sj.day_type_id] = {"journeys": set(), "lines": set()}
            balance[sj.day_type_id]["journeys"].add(sj.id)
            balance[sj.day_type_id]["lines"].add(sj.line_id)

        return {
            dtid: {
                "journey_count": len(data["journeys"]),
                "line_count": len(data["lines"]),
            }
            for dtid, data in balance.items()
        }

    def summary(self) -> Dict:
        """High-level dataset summary."""
        return {
            "operators": self.dataset.operators.count(),
            "lines": self.dataset.lines.count(),
            "stops": self.dataset.scheduled_stop_points.count(),
            "day_types": self.dataset.day_types.count(),
            "journey_patterns": self.dataset.journey_patterns.count(),
            "service_journeys": self.dataset.service_journeys.count(),
            "passing_times": self.dataset.passing_times.count(),
            "shapes": len(self.dataset.shape_points.get_shape_ids()) if hasattr(self.dataset, 'shape_points') else 0,
            "frequencies": self.dataset.frequencies.count() if hasattr(self.dataset, 'frequencies') else 0,
            "transfers": self.dataset.transfers.count() if hasattr(self.dataset, 'transfers') else 0,
            "service_balance": self.service_balance(),
        }
