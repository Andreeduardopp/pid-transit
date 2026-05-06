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
    TransportMode,
    DirectionType
)

__version__ = "0.2.0"

__all__ = [
    "TransitDataset",
    "GtfsImporter",
    "GtfsExporter",
    "NetexImporter",
    "NetexExporter",
    "SpreadsheetImporter",
    
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
    "TransportMode",
    "DirectionType"
]
