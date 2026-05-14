"""Tests for pid_transit.core.repositories."""

import pytest

from pid_transit.core.dataset import TransitDataset
from pid_transit.core.repositories import (
    OperatorRepository,
    LineRepository,
    ScheduledStopPointRepository,
    DayTypeRepository,
    OperatingDayExceptionRepository,
    JourneyPatternRepository,
    PointInJourneyPatternRepository,
    ServiceJourneyRepository,
    PassingTimeRepository,
)
from pid_transit.core.exceptions import EntityNotFoundError
from pid_transit.core.schemas import (
    Operator, Line, ScheduledStopPoint, DayType,
    JourneyPattern, PointInJourneyPattern, ServiceJourney, PassingTime,
    TransportMode, DirectionType,
)


@pytest.fixture
def dataset(populated_db):
    ds = TransitDataset.__new__(TransitDataset)
    ds.db = populated_db
    ds.operators = OperatorRepository(populated_db)
    ds.lines = LineRepository(populated_db)
    ds.scheduled_stop_points = ScheduledStopPointRepository(populated_db)
    ds.day_types = DayTypeRepository(populated_db)
    ds.operating_day_exceptions = OperatingDayExceptionRepository(populated_db)
    ds.journey_patterns = JourneyPatternRepository(populated_db)
    ds.points_in_journey_pattern = PointInJourneyPatternRepository(populated_db)
    ds.service_journeys = ServiceJourneyRepository(populated_db)
    ds.passing_times = PassingTimeRepository(populated_db)
    return ds


class TestBaseRepositoryOperations:
    def test_get_all(self, dataset):
        ops = dataset.operators.get_all()
        assert len(ops) == 1
        assert isinstance(ops[0], Operator)

    def test_get_by_id(self, dataset):
        op = dataset.operators.get_by_id("OP1")
        assert op.name == "Test Agency"

    def test_get_by_id_not_found(self, dataset):
        with pytest.raises(EntityNotFoundError):
            dataset.operators.get_by_id("NOPE")

    def test_count(self, dataset):
        assert dataset.scheduled_stop_points.count() == 2

    def test_count_filtered(self, dataset):
        assert dataset.scheduled_stop_points.count(where={"id": "S1"}) == 1

    def test_query(self, dataset):
        results = dataset.scheduled_stop_points.query(where={"id": "S1"})
        assert len(results) == 1
        assert isinstance(results[0], ScheduledStopPoint)

    def test_add(self, dataset):
        new_op = Operator(id="OP2", name="New Agency", timezone="UTC")
        dataset.operators.add(new_op)
        assert dataset.operators.count() == 2

    def test_add_many(self, dataset):
        stops = [
            ScheduledStopPoint(id="S10", name="New Stop A", lat=41.0, lon=-73.0),
            ScheduledStopPoint(id="S11", name="New Stop B", lat=41.1, lon=-73.1),
        ]
        dataset.scheduled_stop_points.add_many(stops)
        assert dataset.scheduled_stop_points.count() == 4

    def test_delete(self, dataset):
        new_op = Operator(id="OP_TEMP", name="Temporary", timezone="UTC")
        dataset.operators.add(new_op)
        assert dataset.operators.delete("OP_TEMP") is True
        assert dataset.operators.count() == 1

    def test_delete_nonexistent(self, dataset):
        assert dataset.operators.delete("NOPE") is False

    def test_delete_many(self, dataset):
        stops = [
            ScheduledStopPoint(id="TMP1", name="Temp A", lat=41.0, lon=-73.0),
            ScheduledStopPoint(id="TMP2", name="Temp B", lat=41.1, lon=-73.1),
        ]
        dataset.scheduled_stop_points.add_many(stops)
        n = dataset.scheduled_stop_points.delete_many(["TMP1", "TMP2"])
        assert n == 2

    def test_update(self, dataset):
        updated = dataset.operators.update("OP1", {"name": "Renamed Agency"})
        assert updated.name == "Renamed Agency"
        assert updated.id == "OP1"
        fetched = dataset.operators.get_by_id("OP1")
        assert fetched.name == "Renamed Agency"

    def test_update_not_found(self, dataset):
        with pytest.raises(EntityNotFoundError):
            dataset.operators.update("NOPE", {"name": "X"})

    def test_update_validates(self, dataset):
        with pytest.raises(Exception):
            dataset.scheduled_stop_points.update("S1", {"lat": 999})


class TestLineRepository:
    def test_get_by_operator(self, dataset):
        lines = dataset.lines.get_by_operator("OP1")
        assert len(lines) == 1
        assert lines[0].id == "L1"

    def test_get_by_operator_no_match(self, dataset):
        assert dataset.lines.get_by_operator("NOPE") == []


class TestJourneyPatternRepository:
    def test_get_by_line(self, dataset):
        jps = dataset.journey_patterns.get_by_line("L1")
        assert len(jps) == 1
        assert isinstance(jps[0], JourneyPattern)


class TestPointInJourneyPatternRepository:
    def test_get_by_pattern_ordered(self, dataset):
        points = dataset.points_in_journey_pattern.get_by_pattern("JP_T1")
        assert len(points) == 2
        assert points[0].order < points[1].order
        assert points[0].stop_point_id == "S1"
        assert points[1].stop_point_id == "S2"


class TestServiceJourneyRepository:
    def test_get_by_line(self, dataset):
        sjs = dataset.service_journeys.get_by_line("L1")
        assert len(sjs) == 1
        assert sjs[0].id == "T1"

    def test_get_by_day_type(self, dataset):
        sjs = dataset.service_journeys.get_by_day_type("WEEKDAY")
        assert len(sjs) == 1


class TestPassingTimeRepository:
    def test_get_by_journey_ordered(self, dataset):
        pts = dataset.passing_times.get_by_journey("T1")
        assert len(pts) == 2
        assert pts[0].order < pts[1].order
        assert pts[0].arrival_time == "08:00:00"
        assert pts[1].arrival_time == "08:10:00"
