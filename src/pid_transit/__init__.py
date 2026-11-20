"""
PID-Transit: A professional transit data library based on Transmodel.

Public names resolve lazily (PEP 562): importing this package pulls in nothing
but the standard library, and each subsystem — with whatever it depends on — is
loaded on the first attribute access that needs it. A caller converting GTFS to
NeTEx therefore never pays for `openpyxl` or the GTFS-RT stack.
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Static mirror of the lazy map below, so type checkers and IDEs still
    # resolve every public name. Kept in sync by tests/test_lazy_imports.py.
    from .core.dataset import TransitDataset
    from .convert import (
        ConversionResult,
        convert,
        gtfs_to_netex,
        netex_to_gtfs,
    )
    from .adapters.gtfs_importer import GtfsImporter
    from .adapters.gtfs_exporter import GtfsExporter
    from .adapters.netex_importer import NetexImporter
    from .adapters.netex_exporter import NetexExporter
    from .adapters.spreadsheet_importer import SpreadsheetImporter
    from .analytics.statistics import TransitStatistics
    from .analytics.diff import FeedDiffer, FeedDiffReport
    from .analytics.merge import FeedMerger
    from .analytics.rt_readiness import check_rt_readiness, RTReadinessReport
    from .adapters.gtfs_rt import (
        RtSnapshot,
        RtTripUpdate,
        RtVehiclePosition,
        RtAlert,
        RtMatchReport,
        fetch_snapshot,
        parse_feed,
        match_snapshot,
    )
    from .core.schemas import (
        Operator,
        Line,
        ScheduledStopPoint,
        Level,
        StopArea,
        Pathway,
        DayType,
        OperatingDayException,
        JourneyPattern,
        PointInJourneyPattern,
        ServiceJourney,
        PassingTime,
        FeedInfo,
        ShapePoint,
        Frequency,
        Transfer,
        Translation,
        Attribution,
        FareAttribute,
        FareRule,
        TransportMode,
        DirectionType,
    )

__version__ = "0.7.4"

# Submodule -> the public names it defines. Resolved on first access.
_LAZY_MODULES: dict[str, tuple[str, ...]] = {
    ".core.dataset": ("TransitDataset",),
    ".convert": ("ConversionResult", "convert", "gtfs_to_netex", "netex_to_gtfs"),
    ".adapters.gtfs_importer": ("GtfsImporter",),
    ".adapters.gtfs_exporter": ("GtfsExporter",),
    ".adapters.netex_importer": ("NetexImporter",),
    ".adapters.netex_exporter": ("NetexExporter",),
    ".adapters.spreadsheet_importer": ("SpreadsheetImporter",),
    ".analytics.statistics": ("TransitStatistics",),
    ".analytics.diff": ("FeedDiffer", "FeedDiffReport"),
    ".analytics.merge": ("FeedMerger",),
    ".analytics.rt_readiness": ("check_rt_readiness", "RTReadinessReport"),
    ".adapters.gtfs_rt": (
        "RtSnapshot",
        "RtTripUpdate",
        "RtVehiclePosition",
        "RtAlert",
        "RtMatchReport",
        "fetch_snapshot",
        "parse_feed",
        "match_snapshot",
    ),
    ".core.schemas": (
        "Operator",
        "Line",
        "ScheduledStopPoint",
        "Level",
        "StopArea",
        "Pathway",
        "DayType",
        "OperatingDayException",
        "JourneyPattern",
        "PointInJourneyPattern",
        "ServiceJourney",
        "PassingTime",
        "FeedInfo",
        "ShapePoint",
        "Frequency",
        "Transfer",
        "Translation",
        "Attribution",
        "FareAttribute",
        "FareRule",
        "TransportMode",
        "DirectionType",
    ),
}

_LAZY_ATTRS: dict[str, str] = {
    name: module for module, names in _LAZY_MODULES.items() for name in names
}

# Subpackages stay reachable as attributes (`pid_transit.core`), as they were
# when the imports above ran eagerly.
_SUBPACKAGES = ("core", "adapters", "analytics")


def __getattr__(name: str) -> object:
    """Resolve a public name on first access (PEP 562)."""
    module = _LAZY_ATTRS.get(name)
    if module is not None:
        value = getattr(import_module(module, __name__), name)
    elif name in _SUBPACKAGES:
        value = import_module(f".{name}", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    # Cache in the module namespace: __getattr__ runs once per name.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__, *_SUBPACKAGES})


__all__ = [
    "TransitDataset",
    "ConversionResult",
    "convert",
    "gtfs_to_netex",
    "netex_to_gtfs",
    "GtfsImporter",
    "GtfsExporter",
    "NetexImporter",
    "NetexExporter",
    "SpreadsheetImporter",
    "TransitStatistics",
    "FeedDiffer",
    "FeedDiffReport",
    "FeedMerger",
    "check_rt_readiness",
    "RTReadinessReport",
    "RtSnapshot",
    "RtTripUpdate",
    "RtVehiclePosition",
    "RtAlert",
    "RtMatchReport",
    "fetch_snapshot",
    "parse_feed",
    "match_snapshot",

    # Models
    "Operator",
    "Line",
    "ScheduledStopPoint",
    "Level",
    "StopArea",
    "Pathway",
    "DayType",
    "OperatingDayException",
    "JourneyPattern",
    "PointInJourneyPattern",
    "ServiceJourney",
    "PassingTime",
    "FeedInfo",
    "ShapePoint",
    "Frequency",
    "Transfer",
    "Translation",
    "Attribution",
    "FareAttribute",
    "FareRule",
    "TransportMode",
    "DirectionType",
]
