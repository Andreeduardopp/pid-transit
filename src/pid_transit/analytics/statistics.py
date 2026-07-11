"""
Operational statistics for transit datasets.

Every equation of docs/formalization.md §3 is executed as a SQL aggregation
against the underlying database rather than by materializing tables as Pydantic
objects. The rows were validated at insert time, so re-validating millions of
them to compute simple aggregations buys nothing but memory pressure; on the
Swiss national feed this keeps `stats` fast with flat memory. The public API and
return shapes are unchanged.

The one nontrivial primitive is τ (time string → seconds, supporting HH ≥ 24 for
overnight service). It is registered on each connection as the SQL function
`tau(t)`.
"""

from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple

from ..core.dataset import TransitDataset


def _time_to_seconds(t: str) -> int:
    parts = t.strip().split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def _seconds_to_time(s: int) -> str:
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _tau(t: Optional[str]) -> Optional[int]:
    """τ: GTFS time string → seconds since service-day start (HH may be ≥ 24)."""
    if not t:
        return None
    try:
        return _time_to_seconds(t)
    except (ValueError, IndexError):
        return None


class TransitStatistics:
    """Computes operational statistics from a TransitDataset."""

    def __init__(
        self,
        dataset: TransitDataset,
        peak_hours: Optional[List[Tuple[str, str]]] = None,
    ):
        self.dataset = dataset
        self.peak_hours = peak_hours or [("07:00:00", "09:00:00"), ("17:00:00", "19:00:00")]

    @contextmanager
    def _cursor(self) -> Iterator:
        """A connection with the τ function registered, released afterwards."""
        conn = self.dataset.db.connect()
        conn.create_function("tau", 1, _tau, deterministic=True)
        try:
            yield conn
        finally:
            self.dataset.db._release(conn)

    def _is_peak(self, time_str: str) -> bool:
        t = _time_to_seconds(time_str)
        for start, end in self.peak_hours:
            if _time_to_seconds(start) <= t < _time_to_seconds(end):
                return True
        return False

    def service_span(
        self, line_id: Optional[str] = None
    ) -> Dict[str, Dict[str, Dict[str, str]]]:
        """First and last departure per line per day type (§3.3).

        Returns: {line_id: {day_type_id: {"first": HH:MM:SS, "last": HH:MM:SS}}}
        """
        sql = (
            "SELECT line_id, day_type_id, "
            "MIN(tau(departure_time)) AS first_s, MAX(tau(departure_time)) AS last_s "
            "FROM service_journey WHERE departure_time IS NOT NULL"
        )
        params: list = []
        if line_id:
            sql += " AND line_id = ?"
            params.append(line_id)
        sql += " GROUP BY line_id, day_type_id"

        result: Dict[str, Dict[str, Dict[str, str]]] = {}
        with self._cursor() as conn:
            for lid, dtid, first_s, last_s in conn.execute(sql, params):
                if first_s is None:
                    continue
                result.setdefault(lid, {})[dtid] = {
                    "first": _seconds_to_time(first_s),
                    "last": _seconds_to_time(last_s),
                }
        return result

    def headways(
        self, line_id: str, day_type_id: str
    ) -> Dict[str, Optional[float]]:
        """Headway statistics for a line on a day type (§3.1), seconds.

        Returns: {"avg", "min", "max", "peak_avg", "offpeak_avg"}.
        """
        with self._cursor() as conn:
            rows = conn.execute(
                "SELECT tau(departure_time) AS s FROM service_journey "
                "WHERE line_id = ? AND day_type_id = ? AND departure_time IS NOT NULL "
                "ORDER BY s",
                (line_id, day_type_id),
            ).fetchall()

        times = [r[0] for r in rows if r[0] is not None]
        if len(times) < 2:
            return {"avg": None, "min": None, "max": None,
                    "peak_avg": None, "offpeak_avg": None}

        gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]

        peak_gaps: List[int] = []
        offpeak_gaps: List[int] = []
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
        """Total vehicle-hours: Σ (max τ − min τ) per journey (§3.2).

        Only journeys with at least two timed passing times contribute (the
        |Π_j| ≥ 2 rule); COALESCE prefers departure over arrival, matching the
        object-based path.
        """
        # Qualify with the pt alias: the day_type_id branch JOINs service_journey,
        # which also has departure_time/arrival_time columns (would be ambiguous).
        time_expr = "tau(COALESCE(pt.departure_time, pt.arrival_time))"
        sql = (
            f"SELECT SUM(span) FROM ("
            f"  SELECT MAX({time_expr}) - MIN({time_expr}) AS span "
            f"  FROM passing_time pt "
        )
        params: list = []
        if day_type_id:
            sql += "  JOIN service_journey sj ON pt.service_journey_id = sj.id "
            sql += "  WHERE sj.day_type_id = ? "
            params.append(day_type_id)
        sql += (
            f"  GROUP BY pt.service_journey_id "
            f"  HAVING COUNT({time_expr}) >= 2"
            f")"
        )
        with self._cursor() as conn:
            total = conn.execute(sql, params).fetchone()[0]
        return (total or 0) / 3600.0

    def stop_coverage(
        self, day_type_id: Optional[str] = None
    ) -> Dict[str, int]:
        """Count departures per stop (§3.4).

        Returns: {stop_id: departure_count}
        """
        sql = "SELECT pt.stop_point_id, COUNT(*) FROM passing_time pt "
        params: list = []
        if day_type_id:
            sql += "JOIN service_journey sj ON pt.service_journey_id = sj.id "
            sql += "WHERE sj.day_type_id = ? AND pt.departure_time IS NOT NULL AND pt.departure_time != '' "
            params.append(day_type_id)
        else:
            sql += "WHERE pt.departure_time IS NOT NULL AND pt.departure_time != '' "
        sql += "GROUP BY pt.stop_point_id"

        with self._cursor() as conn:
            return {stop_id: count for stop_id, count in conn.execute(sql, params)}

    def service_balance(self) -> Dict[str, Dict[str, int]]:
        """Journeys and lines per day type (§3.4).

        Returns: {day_type_id: {"journey_count": N, "line_count": N}}
        """
        sql = (
            "SELECT day_type_id, COUNT(DISTINCT id) AS journeys, "
            "COUNT(DISTINCT line_id) AS lines FROM service_journey GROUP BY day_type_id"
        )
        with self._cursor() as conn:
            return {
                dtid: {"journey_count": journeys, "line_count": lines}
                for dtid, journeys, lines in conn.execute(sql)
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
