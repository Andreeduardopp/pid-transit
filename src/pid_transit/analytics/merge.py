"""
Multi-feed merge tool.

Merges one or more source datasets into a target dataset,
namespacing IDs to prevent collisions and optionally deduplicating
nearby stops.
"""

import math
import logging
from typing import Dict, List, Optional, Set, Tuple

from ..core.dataset import TransitDataset
from ..core.schemas import TRANSMODEL_ENTITIES

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M = 6_371_000

_ID_TABLES = [
    "operator", "line", "scheduled_stop_point", "day_type",
    "journey_pattern", "service_journey", "feed_info",
]

_FK_MAP = {
    "line": {"operator_id": "operator"},
    "journey_pattern": {"line_id": "line"},
    "service_journey": {"line_id": "line", "journey_pattern_id": "journey_pattern",
                        "day_type_id": "day_type"},
    "operating_day_exception": {"day_type_id": "day_type"},
    "point_in_journey_pattern": {"journey_pattern_id": "journey_pattern",
                                 "stop_point_id": "scheduled_stop_point"},
    "passing_time": {"service_journey_id": "service_journey",
                     "stop_point_id": "scheduled_stop_point"},
    "frequency": {"service_journey_id": "service_journey"},
    "transfer": {"from_stop_id": "scheduled_stop_point",
                 "to_stop_id": "scheduled_stop_point"},
    "shape_point": {},
}


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two lat/lon points."""
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


class FeedMerger:
    """Merges source datasets into a target dataset with ID namespacing."""

    def __init__(self, target: TransitDataset, namespace_separator: str = ":"):
        self.target = target
        self.sep = namespace_separator

    def merge(
        self,
        source: TransitDataset,
        namespace: str,
        merge_stops_threshold_m: Optional[float] = None,
    ) -> Dict[str, int]:
        """Merge a source dataset into the target.

        Args:
            source: The dataset to merge from.
            namespace: Prefix for all source IDs (e.g., "stcp").
            merge_stops_threshold_m: If set, merge stops within this
                distance (meters) to existing target stops instead of
                creating duplicates.

        Returns:
            Dict of table -> records merged count.
        """
        prefix = f"{namespace}{self.sep}"
        id_remap: Dict[str, Dict[str, str]] = {t: {} for t in _ID_TABLES}
        stats: Dict[str, int] = {}

        # 1. Build stop proximity map if requested
        stop_proximity_map: Dict[str, str] = {}
        if merge_stops_threshold_m is not None:
            stop_proximity_map = self._build_stop_proximity_map(
                source, merge_stops_threshold_m
            )

        # 2. Process ID tables in FK-safe order
        ordered_tables = [
            "feed_info", "operator", "scheduled_stop_point", "day_type",
            "line", "journey_pattern", "service_journey",
        ]

        for table in ordered_tables:
            src_records = source.db.get_records(table)
            if not src_records:
                continue

            new_records = []
            for rec in src_records:
                new_rec = dict(rec)
                old_id = rec["id"]
                if table == "scheduled_stop_point" and old_id in stop_proximity_map:
                    id_remap[table][old_id] = stop_proximity_map[old_id]
                    continue
                new_id = f"{prefix}{old_id}"
                new_rec["id"] = new_id
                id_remap[table][old_id] = new_id

                fk_defs = _FK_MAP.get(table, {})
                for fk_field, ref_table in fk_defs.items():
                    if new_rec.get(fk_field):
                        new_rec[fk_field] = id_remap[ref_table].get(
                            new_rec[fk_field], f"{prefix}{new_rec[fk_field]}"
                        )
                new_records.append(new_rec)

            if new_records:
                stats[table] = self.target.db.upsert(table, new_records)

        # 3. Process composite-key tables
        composite_tables = [
            "operating_day_exception", "point_in_journey_pattern",
            "passing_time", "frequency", "transfer",
        ]

        for table in composite_tables:
            src_records = source.db.get_records(table)
            if not src_records:
                continue

            new_records = []
            fk_defs = _FK_MAP.get(table, {})
            for rec in src_records:
                new_rec = dict(rec)
                for fk_field, ref_table in fk_defs.items():
                    if new_rec.get(fk_field):
                        new_rec[fk_field] = id_remap[ref_table].get(
                            new_rec[fk_field], f"{prefix}{new_rec[fk_field]}"
                        )
                new_records.append(new_rec)

            if new_records:
                stats[table] = self.target.db.upsert(table, new_records)

        # 4. Shape points (namespace the shape_id)
        shape_records = source.db.get_records("shape_point")
        if shape_records:
            for rec in shape_records:
                rec["shape_id"] = f"{prefix}{rec['shape_id']}"
            stats["shape_point"] = self.target.db.upsert("shape_point", shape_records)

        return stats

    def _build_stop_proximity_map(
        self, source: TransitDataset, threshold_m: float
    ) -> Dict[str, str]:
        """Map source stop IDs to existing target stop IDs if within threshold."""
        target_stops = self.target.db.get_records("scheduled_stop_point")
        source_stops = source.db.get_records("scheduled_stop_point")

        mapping: Dict[str, str] = {}
        for src in source_stops:
            best_dist = threshold_m
            best_id = None
            for tgt in target_stops:
                d = _haversine(src["lat"], src["lon"], tgt["lat"], tgt["lon"])
                if d < best_dist:
                    best_dist = d
                    best_id = tgt["id"]
            if best_id is not None:
                mapping[src["id"]] = best_id

        if mapping:
            logger.info(f"Proximity-merged {len(mapping)} stops (threshold={threshold_m}m)")

        return mapping
