"""
PID-Transit: A professional transit data library based on Transmodel.
"""

from .core.dataset import TransitDataset
from .adapters.gtfs_importer import GtfsImporter
from .adapters.gtfs_exporter import GtfsExporter
from .adapters.netex_importer import NetexImporter
from .adapters.netex_exporter import NetexExporter
from .adapters.spreadsheet_importer import SpreadsheetImporter

# Expose domain models directly for ease of use
from .core.schemas import (
    Operator,
    Line,
    ScheduledStopPoint,
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
    TransportMode,
    DirectionType,
)
from .analytics.statistics import TransitStatistics
from .analytics.diff import FeedDiffer, FeedDiffReport
from .analytics.merge import FeedMerger
from .analytics.rt_readiness import check_rt_readiness, RTReadinessReport

__version__ = "0.7.0"

__all__ = [
    "TransitDataset",
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

    # Models
    "Operator",
    "Line",
    "ScheduledStopPoint",
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
    "TransportMode",
    "DirectionType",
]
