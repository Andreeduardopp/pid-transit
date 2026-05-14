"""
Repository pattern for Transmodel entities.

This module provides an object-oriented data access layer over the
raw SQLite database wrapper, returning fully validated Pydantic models.
"""

from typing import TypeVar, Generic, List, Optional, Type, Any, Dict
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

    def get_by_id(self, entity_id: str) -> T:
        """Fetch a single entity by its primary key 'id' field."""
        record = self.db.get_one(self.table_name, where={"id": entity_id})
        if record is None:
            raise EntityNotFoundError(
                f"{self.model_class.__name__} with id '{entity_id}' not found"
            )
        return self.model_class(**record)

    def get_by_field(self, field: str, value: Any) -> List[T]:
        """Fetch all entities where a field matches a value."""
        records = self.db.query(self.table_name, where={field: value})
        return [self.model_class(**r) for r in records]

    def query(
        self,
        where: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[T]:
        """Fetch entities with optional filtering, ordering, and limit."""
        records = self.db.query(
            self.table_name, where=where, order_by=order_by, limit=limit
        )
        return [self.model_class(**r) for r in records]

    def count(self, where: Optional[Dict[str, Any]] = None) -> int:
        """Return the number of records, optionally filtered."""
        return self.db.count(self.table_name, where=where)

    def add(self, entity: T) -> None:
        """Upsert a single entity."""
        self.db.upsert(self.table_name, [entity.model_dump()])

    def add_many(self, entities: List[T]) -> None:
        """Upsert multiple entities efficiently."""
        self.db.upsert(self.table_name, [e.model_dump() for e in entities])

    def delete(self, entity_id: str) -> bool:
        """Delete a single entity by ID. Returns True if deleted."""
        return self.db.delete(self.table_name, where={"id": entity_id}) > 0

    def delete_many(self, entity_ids: List[str]) -> int:
        """Delete multiple entities by ID. Returns count deleted."""
        total = 0
        for eid in entity_ids:
            total += self.db.delete(self.table_name, where={"id": eid})
        return total

    def update(self, entity_id: str, updates: Dict[str, Any]) -> T:
        """Partial update: fetch by ID, merge updates, validate, and upsert."""
        record = self.db.get_one(self.table_name, where={"id": entity_id})
        if record is None:
            raise EntityNotFoundError(
                f"{self.model_class.__name__} with id '{entity_id}' not found"
            )
        merged = {**record, **updates}
        validated = self.model_class(**merged)
        self.db.upsert(self.table_name, [validated.model_dump()])
        return validated


from .schemas import (
    Operator, Line, ScheduledStopPoint, DayType,
    OperatingDayException, JourneyPattern, PointInJourneyPattern,
    ServiceJourney, PassingTime,
    FeedInfo, ShapePoint, Frequency, Transfer,
)


class OperatorRepository(BaseRepository[Operator]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "operator", Operator)


class LineRepository(BaseRepository[Line]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "line", Line)

    def get_by_operator(self, operator_id: str) -> List[Line]:
        return self.get_by_field("operator_id", operator_id)


class ScheduledStopPointRepository(BaseRepository[ScheduledStopPoint]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "scheduled_stop_point", ScheduledStopPoint)


class DayTypeRepository(BaseRepository[DayType]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "day_type", DayType)


class OperatingDayExceptionRepository(BaseRepository[OperatingDayException]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "operating_day_exception", OperatingDayException)

    def get_by_day_type(self, day_type_id: str) -> List[OperatingDayException]:
        return self.get_by_field("day_type_id", day_type_id)


class JourneyPatternRepository(BaseRepository[JourneyPattern]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "journey_pattern", JourneyPattern)

    def get_by_line(self, line_id: str) -> List[JourneyPattern]:
        return self.get_by_field("line_id", line_id)


class PointInJourneyPatternRepository(BaseRepository[PointInJourneyPattern]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "point_in_journey_pattern", PointInJourneyPattern)

    def get_by_pattern(self, pattern_id: str) -> List[PointInJourneyPattern]:
        records = self.db.query(
            self.table_name,
            where={"journey_pattern_id": pattern_id},
            order_by="order",
        )
        return [self.model_class(**r) for r in records]


class ServiceJourneyRepository(BaseRepository[ServiceJourney]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "service_journey", ServiceJourney)

    def get_by_line(self, line_id: str) -> List[ServiceJourney]:
        return self.get_by_field("line_id", line_id)

    def get_by_day_type(self, day_type_id: str) -> List[ServiceJourney]:
        return self.get_by_field("day_type_id", day_type_id)


class PassingTimeRepository(BaseRepository[PassingTime]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "passing_time", PassingTime)

    def get_by_journey(self, journey_id: str) -> List[PassingTime]:
        records = self.db.query(
            self.table_name,
            where={"service_journey_id": journey_id},
            order_by="order",
        )
        return [self.model_class(**r) for r in records]


class FeedInfoRepository(BaseRepository[FeedInfo]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "feed_info", FeedInfo)


class ShapePointRepository(BaseRepository[ShapePoint]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "shape_point", ShapePoint)

    def get_by_shape(self, shape_id: str) -> List[ShapePoint]:
        records = self.db.query(
            self.table_name,
            where={"shape_id": shape_id},
            order_by="sequence",
        )
        return [self.model_class(**r) for r in records]

    def get_shape_ids(self) -> List[str]:
        conn = self.db.connect()
        try:
            cur = conn.execute("SELECT DISTINCT shape_id FROM shape_point ORDER BY shape_id")
            return [row[0] for row in cur.fetchall()]
        finally:
            if not self.db._in_memory:
                conn.close()


class FrequencyRepository(BaseRepository[Frequency]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "frequency", Frequency)

    def get_by_journey(self, journey_id: str) -> List[Frequency]:
        return self.get_by_field("service_journey_id", journey_id)


class TransferRepository(BaseRepository[Transfer]):
    def __init__(self, db: TransmodelDatabase):
        super().__init__(db, "transfer", Transfer)

    def get_by_stop(self, stop_id: str) -> List[Transfer]:
        return self.get_by_field("from_stop_id", stop_id)
