"""
Transmodel-based Pydantic schemas for the Core module.

This module provides the core data structures based on the Transmodel/NeTEx
conceptual model, independent of GTFS or NeTEx serialization formats.
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field

class TransportMode(str, Enum):
    BUS = "bus"
    TRAM = "tram"
    RAIL = "rail"
    METRO = "metro"
    FERRY = "ferry"
    CABLE_CAR = "cable_car"
    OTHER = "other"

class DirectionType(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"
    CLOCKWISE = "clockwise"
    ANTICLOCKWISE = "anticlockwise"

# ═══════════════════════════════════════════════════════════════════════════
# Network Topology
# ═══════════════════════════════════════════════════════════════════════════

class Operator(BaseModel):
    """Corresponds to Transmodel 'Operator' / GTFS 'agency'."""
    id: str = Field(..., description="Unique operator identifier")
    name: str = Field(..., description="Full name of the operator")
    url: Optional[str] = Field(None, description="Operator's main URL")
    timezone: str = Field(..., description="Timezone (e.g., Europe/Lisbon)")
    lang: Optional[str] = Field(None, description="Primary language")
    phone: Optional[str] = Field(None, description="Customer service phone")

class Line(BaseModel):
    """Corresponds to Transmodel 'Line' / GTFS 'route'."""
    id: str = Field(..., description="Unique line identifier")
    operator_id: Optional[str] = Field(None, description="Reference to Operator.id")
    name: str = Field(..., description="Public name of the line")
    short_name: Optional[str] = Field(None, description="Short code/number (e.g., L1)")
    transport_mode: TransportMode = Field(..., description="Primary transport mode")
    color: Optional[str] = Field(None, description="Hex color code for UI presentation")

class ScheduledStopPoint(BaseModel):
    """Corresponds to Transmodel 'ScheduledStopPoint' / GTFS 'stop' (location_type=0)."""
    id: str = Field(..., description="Unique stop point identifier")
    name: str = Field(..., description="Public name of the stop")
    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lon: float = Field(..., ge=-180, le=180, description="Longitude")
    stop_area_id: Optional[str] = Field(None, description="Reference to a parent StopPlace/StopArea")

# ═══════════════════════════════════════════════════════════════════════════
# Timetable and Scheduling
# ═══════════════════════════════════════════════════════════════════════════

class DayType(BaseModel):
    """Corresponds to Transmodel 'DayType' / GTFS 'calendar'."""
    id: str = Field(..., description="Unique day type / service identifier")
    monday: bool = Field(False)
    tuesday: bool = Field(False)
    wednesday: bool = Field(False)
    thursday: bool = Field(False)
    friday: bool = Field(False)
    saturday: bool = Field(False)
    sunday: bool = Field(False)
    start_date: str = Field(..., description="YYYY-MM-DD")
    end_date: str = Field(..., description="YYYY-MM-DD")

class OperatingDayException(BaseModel):
    """Corresponds to Transmodel 'OperatingDay' exceptions / GTFS 'calendar_dates'."""
    day_type_id: str = Field(..., description="Reference to DayType.id")
    date: str = Field(..., description="YYYY-MM-DD")
    is_addition: bool = Field(..., description="True if adding service, False if removing")

class JourneyPattern(BaseModel):
    """Corresponds to Transmodel 'JourneyPattern' (defines the stop sequence)."""
    id: str = Field(..., description="Unique journey pattern identifier")
    line_id: str = Field(..., description="Reference to Line.id")
    direction: Optional[DirectionType] = Field(None)

class PointInJourneyPattern(BaseModel):
    """Corresponds to Transmodel 'PointInJourneyPattern' (stop sequence mapping)."""
    journey_pattern_id: str = Field(..., description="Reference to JourneyPattern.id")
    stop_point_id: str = Field(..., description="Reference to ScheduledStopPoint.id")
    order: int = Field(..., description="0-indexed sequence order")

class ServiceJourney(BaseModel):
    """Corresponds to Transmodel 'ServiceJourney' / GTFS 'trip'."""
    id: str = Field(..., description="Unique service journey identifier")
    line_id: str = Field(..., description="Reference to Line.id")
    journey_pattern_id: Optional[str] = Field(None, description="Reference to JourneyPattern.id")
    day_type_id: str = Field(..., description="Reference to DayType.id")
    departure_time: str = Field(..., description="HH:MM:SS format")

class PassingTime(BaseModel):
    """Corresponds to Transmodel 'PassingTime' / GTFS 'stop_times'."""
    service_journey_id: str = Field(..., description="Reference to ServiceJourney.id")
    stop_point_id: str = Field(..., description="Reference to ScheduledStopPoint.id")
    order: int = Field(..., description="Sequence order matching PointInJourneyPattern")
    arrival_time: Optional[str] = Field(None, description="HH:MM:SS format")
    departure_time: Optional[str] = Field(None, description="HH:MM:SS format")

# Registry of all core models for database iteration
TRANSMODEL_ENTITIES = {
    "operator": Operator,
    "line": Line,
    "scheduled_stop_point": ScheduledStopPoint,
    "day_type": DayType,
    "operating_day_exception": OperatingDayException,
    "journey_pattern": JourneyPattern,
    "point_in_journey_pattern": PointInJourneyPattern,
    "service_journey": ServiceJourney,
    "passing_time": PassingTime,
}
