"""
Feed comparison / diff tool.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Set, Tuple

from ..core.dataset import TransitDataset
from ..core.schemas import TRANSMODEL_ENTITIES


@dataclass
class EntityDiff:
    entity_type: str
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)


@dataclass
class FeedDiffReport:
    entity_diffs: List[EntityDiff] = field(default_factory=list)
    frequency_changes: Dict[str, Any] = field(default_factory=dict)
    service_span_changes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        result = {"entities": {}}
        for ed in self.entity_diffs:
            if ed.has_changes:
                result["entities"][ed.entity_type] = {
                    "added": len(ed.added),
                    "removed": len(ed.removed),
                    "modified": len(ed.modified),
                }
        if self.frequency_changes:
            result["frequency_changes"] = self.frequency_changes
        if self.service_span_changes:
            result["service_span_changes"] = self.service_span_changes
        return result

    def to_markdown(self) -> str:
        lines = ["# Feed Comparison Report", ""]
        lines.append("## Entity Changes")
        lines.append("")
        lines.append("| Entity | Added | Removed | Modified |")
        lines.append("|--------|------:|--------:|---------:|")
        for ed in self.entity_diffs:
            if ed.has_changes:
                lines.append(
                    f"| {ed.entity_type} | {len(ed.added)} | "
                    f"{len(ed.removed)} | {len(ed.modified)} |"
                )
        lines.append("")

        if self.service_span_changes:
            lines.append("## Service Span Changes")
            lines.append("")
            for line_id, change in self.service_span_changes.items():
                lines.append(f"- **{line_id}**: {change}")
            lines.append("")

        return "\n".join(lines)


# Tables with a single 'id' primary key
_ID_TABLES = [
    "operator", "line", "scheduled_stop_point", "level", "stop_area", "pathway",
    "day_type", "journey_pattern", "service_journey", "feed_info",
    "fare_attribute", "attribution",
]

# Tables with composite keys
_COMPOSITE_TABLES = {
    "operating_day_exception": ("day_type_id", "date"),
    "point_in_journey_pattern": ("journey_pattern_id", "order"),
    "passing_time": ("service_journey_id", "order"),
    "shape_point": ("shape_id", "sequence"),
    "frequency": ("service_journey_id", "start_time"),
    "transfer": ("from_stop_id", "to_stop_id"),
    "fare_rule": ("fare_id", "route_id", "origin_id", "destination_id", "contains_id"),
    "translation": ("table_name", "field_name", "language", "record_id", "field_value"),
}


def _make_key(record: Dict, key_fields: Tuple[str, ...]) -> str:
    return "|".join(str(record.get(k, "")) for k in key_fields)


def _records_differ(a: Dict, b: Dict) -> bool:
    all_keys = set(a.keys()) | set(b.keys())
    for k in all_keys:
        if k.startswith("_"):
            continue
        if a.get(k) != b.get(k):
            return True
    return False


class FeedDiffer:
    """Compare two TransitDatasets and produce a diff report."""

    def __init__(self, base: TransitDataset, target: TransitDataset):
        self.base = base
        self.target = target

    def _diff_id_table(self, table: str) -> EntityDiff:
        base_records = {r["id"]: r for r in self.base.db.get_records(table)}
        target_records = {r["id"]: r for r in self.target.db.get_records(table)}

        added = sorted(set(target_records) - set(base_records))
        removed = sorted(set(base_records) - set(target_records))
        modified = []
        for eid in sorted(set(base_records) & set(target_records)):
            if _records_differ(base_records[eid], target_records[eid]):
                modified.append(eid)

        return EntityDiff(table, added, removed, modified)

    def _diff_composite_table(self, table: str, key_fields: Tuple[str, ...]) -> EntityDiff:
        base_raw = self.base.db.get_records(table)
        target_raw = self.target.db.get_records(table)

        base_map = {_make_key(r, key_fields): r for r in base_raw}
        target_map = {_make_key(r, key_fields): r for r in target_raw}

        added = sorted(set(target_map) - set(base_map))
        removed = sorted(set(base_map) - set(target_map))
        modified = []
        for key in sorted(set(base_map) & set(target_map)):
            if _records_differ(base_map[key], target_map[key]):
                modified.append(key)

        return EntityDiff(table, added, removed, modified)

    def _compute_span_changes(self) -> Dict[str, str]:
        from .statistics import TransitStatistics

        base_stats = TransitStatistics(self.base)
        target_stats = TransitStatistics(self.target)
        base_spans = base_stats.service_span()
        target_spans = target_stats.service_span()

        changes = {}
        all_lines = set(base_spans) | set(target_spans)
        for lid in sorted(all_lines):
            if lid not in base_spans:
                changes[lid] = "new line"
            elif lid not in target_spans:
                changes[lid] = "removed"
            else:
                b = base_spans[lid]
                t = target_spans[lid]
                for dtid in set(b) | set(t):
                    if dtid in b and dtid in t:
                        if b[dtid] != t[dtid]:
                            changes[f"{lid}/{dtid}"] = (
                                f"{b[dtid]['first']}-{b[dtid]['last']} -> "
                                f"{t[dtid]['first']}-{t[dtid]['last']}"
                            )
        return changes

    def diff(self) -> FeedDiffReport:
        report = FeedDiffReport()

        for table in _ID_TABLES:
            report.entity_diffs.append(self._diff_id_table(table))

        for table, keys in _COMPOSITE_TABLES.items():
            report.entity_diffs.append(self._diff_composite_table(table, keys))

        report.service_span_changes = self._compute_span_changes()

        return report
