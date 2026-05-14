"""Tests for pid_transit.analytics.statistics."""

import pytest

from pid_transit.core.dataset import TransitDataset
from pid_transit.core.repositories import (
    OperatorRepository, LineRepository, ScheduledStopPointRepository,
    DayTypeRepository, OperatingDayExceptionRepository,
    JourneyPatternRepository, PointInJourneyPatternRepository,
    ServiceJourneyRepository, PassingTimeRepository,
    FeedInfoRepository, ShapePointRepository, FrequencyRepository, TransferRepository,
)
from pid_transit.analytics.statistics import TransitStatistics


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
    ds.feed_info = FeedInfoRepository(populated_db)
    ds.shape_points = ShapePointRepository(populated_db)
    ds.frequencies = FrequencyRepository(populated_db)
    ds.transfers = TransferRepository(populated_db)
    return ds


class TestTransitStatistics:
    def test_summary(self, dataset):
        stats = TransitStatistics(dataset)
        s = stats.summary()
        assert s["operators"] == 1
        assert s["lines"] == 1
        assert s["stops"] == 2
        assert s["service_journeys"] == 1

    def test_service_span(self, dataset):
        stats = TransitStatistics(dataset)
        spans = stats.service_span()
        assert "L1" in spans
        assert "WEEKDAY" in spans["L1"]
        assert spans["L1"]["WEEKDAY"]["first"] == "08:00:00"

    def test_service_span_filtered(self, dataset):
        stats = TransitStatistics(dataset)
        spans = stats.service_span(line_id="L1")
        assert len(spans) == 1

    def test_headways_single_journey(self, dataset):
        stats = TransitStatistics(dataset)
        hw = stats.headways("L1", "WEEKDAY")
        assert hw["avg"] is None

    def test_vehicle_hours(self, dataset):
        stats = TransitStatistics(dataset)
        vh = stats.vehicle_hours()
        assert vh > 0

    def test_stop_coverage(self, dataset):
        stats = TransitStatistics(dataset)
        cov = stats.stop_coverage()
        assert "S1" in cov
        assert cov["S1"] >= 1

    def test_service_balance(self, dataset):
        stats = TransitStatistics(dataset)
        bal = stats.service_balance()
        assert "WEEKDAY" in bal
        assert bal["WEEKDAY"]["journey_count"] == 1
        assert bal["WEEKDAY"]["line_count"] == 1
