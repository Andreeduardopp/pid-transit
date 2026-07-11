"""
Geospatial helpers shared across analytics and validation.

Kept in one place so proximity logic (stop dedup in the merger, duplicate-stop
detection in the validator) has a single, tested implementation.
"""

import math
from typing import Iterable, List, Optional, Tuple

EARTH_RADIUS_M = 6_371_000
_DEG_M = 111_320.0  # meters per degree of latitude


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points."""
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class StopGrid:
    """Spatial hash of points for fast "nearest within threshold" queries.

    Replaces the O(n·m) nested haversine scan used by stop deduplication with an
    O(n+m) bucket lookup. Points are binned on a local equirectangular projection
    (meters east/north about the mean latitude) into square cells of side
    ``threshold_m``; any two points within ``threshold_m`` therefore fall in the
    same or an adjacent cell, so a query only compares against the 3×3 cell
    neighborhood and confirms candidates with an exact :func:`haversine`. Points
    with missing coordinates are dropped from the index.
    """

    def __init__(
        self,
        points: Iterable[Tuple[str, Optional[float], Optional[float]]],
        cell_m: float,
    ):
        if cell_m <= 0:
            raise ValueError("cell_m must be positive")
        self.cell_m = cell_m
        pts = [(pid, lat, lon) for pid, lat, lon in points
               if lat is not None and lon is not None]
        self.lat0 = sum(lat for _, lat, _ in pts) / len(pts) if pts else 0.0
        self.cos0 = math.cos(math.radians(self.lat0)) or 1e-9
        self.buckets: dict = {}
        for pid, lat, lon in pts:
            self.buckets.setdefault(self._cell(lat, lon), []).append((pid, lat, lon))

    def _cell(self, lat: float, lon: float) -> Tuple[int, int]:
        x = lon * self.cos0 * _DEG_M
        y = lat * _DEG_M
        return (int(math.floor(x / self.cell_m)), int(math.floor(y / self.cell_m)))

    def within(
        self, lat: Optional[float], lon: Optional[float], threshold_m: float
    ) -> List[Tuple[str, float]]:
        """All indexed points within ``threshold_m`` (inclusive), as ``(id, distance_m)``.

        ``threshold_m`` must not exceed ``cell_m`` or the 3×3 neighborhood no
        longer covers the search radius.
        """
        if lat is None or lon is None:
            return []
        if threshold_m > self.cell_m:
            raise ValueError("threshold_m must not exceed cell_m")
        cx, cy = self._cell(lat, lon)
        hits: List[Tuple[str, float]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for pid, plat, plon in self.buckets.get((cx + dx, cy + dy), ()):
                    d = haversine(lat, lon, plat, plon)
                    if d <= threshold_m:
                        hits.append((pid, d))
        return hits

    def nearest(
        self, lat: Optional[float], lon: Optional[float], threshold_m: float
    ) -> Optional[Tuple[str, float]]:
        """Nearest indexed point strictly within ``threshold_m``, or ``None``.

        Returns ``(id, distance_m)``. Ties resolve to the first candidate found.
        """
        if lat is None or lon is None:
            return None
        cx, cy = self._cell(lat, lon)
        best_id: Optional[str] = None
        best = threshold_m
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for pid, plat, plon in self.buckets.get((cx + dx, cy + dy), ()):
                    d = haversine(lat, lon, plat, plon)
                    if d < best:
                        best, best_id = d, pid
        return (best_id, best) if best_id is not None else None
