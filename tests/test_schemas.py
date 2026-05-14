"""Tests for pid_transit.core.schemas."""

import pytest
from pydantic import ValidationError

from pid_transit.core.schemas import (
    TRANSMODEL_ENTITIES,
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


class TestOperator:
    def test_valid(self):
        op = Operator(id="A1", name="Agency", timezone="UTC")
        assert op.id == "A1"
        assert op.url is None

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            Operator(id="A1")


class TestLine:
    def test_valid_bus(self):
        line = Line(id="L1", name="Bus 1", transport_mode=TransportMode.BUS)
        assert line.transport_mode == TransportMode.BUS

    def test_all_modes(self):
        for mode in TransportMode:
            line = Line(id="L1", name="test", transport_mode=mode)
            assert line.transport_mode == mode


class TestScheduledStopPoint:
    def test_valid(self):
        s = ScheduledStopPoint(id="S1", name="Main", lat=40.0, lon=-74.0)
        assert s.lat == 40.0

    def test_lat_out_of_range(self):
        with pytest.raises(ValidationError):
            ScheduledStopPoint(id="S1", name="Bad", lat=100.0, lon=0.0)

    def test_lon_out_of_range(self):
        with pytest.raises(ValidationError):
            ScheduledStopPoint(id="S1", name="Bad", lat=0.0, lon=200.0)


class TestDayType:
    def test_valid(self):
        dt = DayType(
            id="WD", monday=True, tuesday=True, wednesday=True,
            thursday=True, friday=True, saturday=False, sunday=False,
            start_date="20260101", end_date="20261231",
        )
        assert dt.monday is True
        assert dt.saturday is False

    def test_defaults_to_false(self):
        dt = DayType(id="EMPTY", start_date="20260101", end_date="20261231")
        assert dt.monday is False
        assert dt.sunday is False


class TestOperatingDayException:
    def test_valid_addition(self):
        e = OperatingDayException(day_type_id="WD", date="20260101", is_addition=True)
        assert e.is_addition is True

    def test_valid_removal(self):
        e = OperatingDayException(day_type_id="WD", date="20260101", is_addition=False)
        assert e.is_addition is False


class TestJourneyPattern:
    def test_valid(self):
        jp = JourneyPattern(id="JP1", line_id="L1", direction=DirectionType.OUTBOUND)
        assert jp.direction == DirectionType.OUTBOUND

    def test_direction_optional(self):
        jp = JourneyPattern(id="JP1", line_id="L1")
        assert jp.direction is None


class TestPointInJourneyPattern:
    def test_valid(self):
        p = PointInJourneyPattern(
            journey_pattern_id="JP1", stop_point_id="S1", order=0,
        )
        assert p.order == 0


class TestServiceJourney:
    def test_valid(self):
        sj = ServiceJourney(
            id="T1", line_id="L1", day_type_id="WD", departure_time="08:00:00",
        )
        assert sj.journey_pattern_id is None

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            ServiceJourney(id="T1")


class TestPassingTime:
    def test_valid(self):
        pt = PassingTime(
            service_journey_id="T1", stop_point_id="S1", order=1,
            arrival_time="08:00:00", departure_time="08:00:00",
        )
        assert pt.order == 1

    def test_times_optional(self):
        pt = PassingTime(
            service_journey_id="T1", stop_point_id="S1", order=1,
        )
        assert pt.arrival_time is None
        assert pt.departure_time is None


class TestFeedInfo:
    def test_valid(self):
        fi = FeedInfo(publisher_name="STCP", publisher_url="https://stcp.pt", lang="pt")
        assert fi.id == "default_feed"
        assert fi.version is None

    def test_with_dates(self):
        fi = FeedInfo(
            publisher_name="STCP", publisher_url="https://stcp.pt",
            lang="pt", start_date="20260101", end_date="20261231", version="1.0",
        )
        assert fi.start_date == "20260101"


class TestShapePoint:
    def test_valid(self):
        sp = ShapePoint(shape_id="SH1", lat=40.0, lon=-74.0, sequence=1)
        assert sp.dist_traveled is None

    def test_with_distance(self):
        sp = ShapePoint(shape_id="SH1", lat=40.0, lon=-74.0, sequence=1, dist_traveled=123.5)
        assert sp.dist_traveled == 123.5


class TestFrequency:
    def test_valid(self):
        f = Frequency(
            service_journey_id="T1", start_time="06:00:00",
            end_time="09:00:00", headway_secs=600,
        )
        assert f.exact_times == 0

    def test_exact_times(self):
        f = Frequency(
            service_journey_id="T1", start_time="06:00:00",
            end_time="09:00:00", headway_secs=600, exact_times=1,
        )
        assert f.exact_times == 1


class TestTransfer:
    def test_valid(self):
        t = Transfer(from_stop_id="S1", to_stop_id="S2", transfer_type=2, min_transfer_time=120)
        assert t.min_transfer_time == 120

    def test_defaults(self):
        t = Transfer(from_stop_id="S1", to_stop_id="S2")
        assert t.transfer_type == 0
        assert t.min_transfer_time is None


class TestAccessibilityFields:
    def test_stop_wheelchair(self):
        s = ScheduledStopPoint(id="S1", name="Main", lat=40.0, lon=-74.0, wheelchair_boarding=1)
        assert s.wheelchair_boarding == 1

    def test_journey_accessibility(self):
        sj = ServiceJourney(
            id="T1", line_id="L1", day_type_id="WD",
            departure_time="08:00:00", wheelchair_accessible=1,
            bikes_allowed=2, shape_id="SH1",
        )
        assert sj.wheelchair_accessible == 1
        assert sj.bikes_allowed == 2
        assert sj.shape_id == "SH1"


class TestEntityRegistry:
    def test_has_all_entities(self):
        expected = {
            "operator", "line", "scheduled_stop_point", "day_type",
            "operating_day_exception", "journey_pattern",
            "point_in_journey_pattern", "service_journey", "passing_time",
            "feed_info", "shape_point", "frequency", "transfer",
        }
        assert expected == set(TRANSMODEL_ENTITIES.keys())

    def test_registry_values_are_model_classes(self):
        for name, cls in TRANSMODEL_ENTITIES.items():
            assert hasattr(cls, "model_dump"), f"{name} is not a Pydantic model"
