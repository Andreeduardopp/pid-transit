"""
Main Facade class for the PID-Transit library.
"""

import logging
from pathlib import Path
from typing import Union, Dict

from .database import TransmodelDatabase
from .repositories import (
    OperatorRepository,
    LineRepository,
    ScheduledStopPointRepository,
    DayTypeRepository,
    OperatingDayExceptionRepository,
    JourneyPatternRepository,
    PointInJourneyPatternRepository,
    ServiceJourneyRepository,
    PassingTimeRepository
)
from .exceptions import ImportFailedError

logger = logging.getLogger(__name__)

class TransitDataset:
    """
    Central entry point for the PID-Transit library.
    
    Provides access to the underlying relational database and exposes
    data via Repository properties.
    """

    def __init__(self, db_path: Union[str, Path]):
        """Initialize the transit dataset linked to a SQLite database."""
        self.db = TransmodelDatabase(db_path)
        
        # Initialize repositories
        self.operators = OperatorRepository(self.db)
        self.lines = LineRepository(self.db)
        self.scheduled_stop_points = ScheduledStopPointRepository(self.db)
        self.day_types = DayTypeRepository(self.db)
        self.operating_day_exceptions = OperatingDayExceptionRepository(self.db)
        self.journey_patterns = JourneyPatternRepository(self.db)
        self.points_in_journey_pattern = PointInJourneyPatternRepository(self.db)
        self.service_journeys = ServiceJourneyRepository(self.db)
        self.passing_times = PassingTimeRepository(self.db)

    def import_data(self, importer, source: Union[str, Path]) -> Dict[str, int]:
        """
        Import data using a provided importer adapter.
        
        Args:
            importer: An instance of an Importer class (e.g., GtfsImporter)
            source: Path or file-like object to import.
            
        Returns:
            Dict mapping table names to number of inserted records.
        """
        try:
            return importer.import_to_db(self.db, source)
        except Exception as e:
            logger.error(f"Import failed: {e}")
            raise ImportFailedError(f"Import failed: {e}") from e

    def export_data(self, exporter, target: Union[str, Path]) -> None:
        """
        Export data using a provided exporter adapter.
        
        Args:
            exporter: An instance of an Exporter class (e.g., GtfsExporter)
            target: Path to save the exported data.
        """
        exporter.export_from_db(self.db, target)

    def validate(self):
        """Run logical validation on the dataset."""
        from .validator import validate_transmodel
        return validate_transmodel(self.db)
