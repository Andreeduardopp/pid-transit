"""Tests for pid_transit.legacy.schemas."""

import pytest
from pydantic import ValidationError

from pid_transit.legacy.schemas import (
    GTFS_TABLE_MODELS,
    GtfsAgency,
    GtfsCalendar,
    GtfsRoute,
    GtfsStop,
    GtfsStopTime,
    GtfsTrip,
    RouteType,
)


class TestGtfsAgency:
    def test_valid(self):
        a = GtfsAgency(
            agency_id="A1",
            agency_name="X",
            agency_url="https://x.com",
            agency_timezone="UTC",
        )
        assert a.agency_id == "A1"

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            GtfsAgency(agency_id="A1")


class TestGtfsStop:
    def test_valid(self):
        s = GtfsStop(stop_id="S1", stop_name="Main", stop_lat=40.0, stop_lon=-74.0)
        assert s.stop_lat == 40.0

    def test_missing_id(self):
        with pytest.raises(ValidationError):
            GtfsStop(stop_name="Main")


class TestGtfsRoute:
    def test_valid_bus(self):
        r = GtfsRoute(route_id="R1", route_type=RouteType.BUS)
        assert r.route_type == 3

    def test_invalid_route_type(self):
        with pytest.raises(ValidationError):
            GtfsRoute(route_id="R1", route_type=999)

    def test_hex_color_validated(self):
        r = GtfsRoute(route_id="R1", route_type=3, route_color="FF0000")
        assert r.route_color == "FF0000"
        with pytest.raises(ValidationError):
            GtfsRoute(route_id="R1", route_type=3, route_color="notahex")


class TestGtfsStopTime:
    def test_valid_time_past_midnight(self):
        st = GtfsStopTime(
            trip_id="T1", stop_id="S1", stop_sequence=1,
            arrival_time="25:35:00", departure_time="25:35:00",
        )
        assert st.arrival_time == "25:35:00"

    def test_invalid_time(self):
        with pytest.raises(ValidationError):
            GtfsStopTime(
                trip_id="T1", stop_id="S1", stop_sequence=1,
                arrival_time="bad time",
            )


class TestGtfsCalendar:
    def test_valid(self):
        c = GtfsCalendar(
            service_id="WD",
            monday=1, tuesday=1, wednesday=1, thursday=1, friday=1,
            saturday=0, sunday=0,
            start_date="20260101", end_date="20261231",
        )
        assert c.start_date == "20260101"

    def test_invalid_date_format(self):
        with pytest.raises(ValidationError):
            GtfsCalendar(
                service_id="WD",
                monday=1, tuesday=1, wednesday=1, thursday=1, friday=1,
                saturday=0, sunday=0,
                start_date="2026-01-01", end_date="20261231",
            )


class TestGtfsTrip:
    def test_valid(self):
        t = GtfsTrip(trip_id="T1", route_id="R1", service_id="WD")
        assert t.trip_id == "T1"


class TestTableModels:
    def test_registry_has_all_tables(self):
        expected = {
            "agency", "stops", "routes", "trips", "stop_times",
            "calendar", "calendar_dates", "shapes", "frequencies",
            "transfers", "feed_info",
            "board_alight", "ridership", "ride_feed_info", "trip_capacity",
        }
        assert expected <= set(GTFS_TABLE_MODELS.keys())
