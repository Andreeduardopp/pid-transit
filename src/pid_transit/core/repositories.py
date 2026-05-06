"""
Repository pattern for Transmodel entities.

This module provides an object-oriented data access layer over the
raw SQLite database wrapper, returning fully validated Pydantic models.
"""

from typing import TypeVar, Generic, List, Optional, Type
from pydantic import BaseModel
from .database import TransmodelDatabase
from .exceptions import EntityNotFoundError

T = TypeVar('T', bound=BaseModel)

class BaseRepository(Generic[T]):
    """Base repository class providing generic CRUD operations."""
    
    def __init__(self, db: TransmodelDatabase, table_name: str, model_class: Type[T]):
        self.db = db
        self.table_name = table_name
        self.model_class = model_class

    def get_all(self) -> List[T]:
        """Fetch all records and return as Pydantic models."""
        records = self.db.get_records(self.table_name)
        return [self.model_class(**record) for record in records]

    def add(self, entity: T) -> None:
        """Upsert a single entity."""
        self.db.upsert(self.table_name, [entity.model_dump()])

    def add_many(self, entities: List[T]) -> None:
        """Upsert multiple entities efficiently."""
        self.db.upsert(self.table_name, [e.model_dump() for e in entities])

    def count(self) -> int:
        """Return the number of records in the repository."""
        conn = self.db.connect()
        try:
            cur = conn.execute(f"SELECT COUNT(*) FROM {self.table_name}")
            return cur.fetchone()[0]
        finally:
            if not self.db._in_memory:
                conn.close()

# Specific Repositories for Type Hinting and Domain-Specific Logic
from .schemas import (
    Operator, Line, ScheduledStopPoint, DayType, 
    OperatingDayException, JourneyPattern, PointInJourneyPattern, 
    ServiceJourney, PassingTime
)

class OperatorRepository(BaseRepository[Operator]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "operator", Operator)

class LineRepository(BaseRepository[Line]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "line", Line)

class ScheduledStopPointRepository(BaseRepository[ScheduledStopPoint]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "scheduled_stop_point", ScheduledStopPoint)

class DayTypeRepository(BaseRepository[DayType]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "day_type", DayType)

class OperatingDayExceptionRepository(BaseRepository[OperatingDayException]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "operating_day_exception", OperatingDayException)

class JourneyPatternRepository(BaseRepository[JourneyPattern]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "journey_pattern", JourneyPattern)

class PointInJourneyPatternRepository(BaseRepository[PointInJourneyPattern]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "point_in_journey_pattern", PointInJourneyPattern)

class ServiceJourneyRepository(BaseRepository[ServiceJourney]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "service_journey", ServiceJourney)

class PassingTimeRepository(BaseRepository[PassingTime]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "passing_time", PassingTime)
