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
    CABLE_CAR = "cable_car"      # street-level cable-pulled (GTFS route_type 5)
    GONDOLA = "gondola"          # aerial lift (GTFS route_type 6)
    FUNICULAR = "funicular"      # GTFS route_type 7
    TROLLEYBUS = "trolleybus"    # GTFS route_type 11
    MONORAIL = "monorail"        # GTFS route_type 12
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
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class Line(BaseModel):
    """Corresponds to Transmodel 'Line' / GTFS 'route'."""
    id: str = Field(..., description="Unique line identifier")
    operator_id: Optional[str] = Field(None, description="Reference to Operator.id")
    name: str = Field(..., description="Public name of the line")
    short_name: Optional[str] = Field(None, description="Short code/number (e.g., L1)")
    transport_mode: TransportMode = Field(..., description="Primary transport mode")
    color: Optional[str] = Field(None, description="Hex color code for UI presentation")
    text_color: Optional[str] = Field(None, description="Hex color for text drawn on `color`")
    description: Optional[str] = Field(None, description="Free-text description (GTFS route_desc)")
    url: Optional[str] = Field(None, description="Line web page (GTFS route_url)")
    sort_order: Optional[int] = Field(None, description="Presentation ordering (GTFS route_sort_order)")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class ScheduledStopPoint(BaseModel):
    """Corresponds to Transmodel 'ScheduledStopPoint' / GTFS 'stop' (location_type=0)."""
    id: str = Field(..., description="Unique stop point identifier")
    name: str = Field(..., description="Public name of the stop")
    lat: Optional[float] = Field(None, ge=-90, le=90, description="Latitude (None if unknown)")
    lon: Optional[float] = Field(None, ge=-180, le=180, description="Longitude (None if unknown)")
    stop_area_id: Optional[str] = Field(None, description="Reference to a parent StopPlace/StopArea")
    wheelchair_boarding: Optional[int] = Field(None, description="0=no info, 1=yes, 2=no")
    code: Optional[str] = Field(None, description="Rider-facing stop code (GTFS stop_code)")
    platform_code: Optional[str] = Field(None, description="Platform designation (GTFS platform_code)")
    level_id: Optional[str] = Field(None, description="Reference to Level.id (GTFS level_id)")
    zone_id: Optional[str] = Field(None, description="Fare zone; referenced by FareRule origin/destination")
    description: Optional[str] = Field(None, description="Free-text description (GTFS stop_desc)")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

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
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class OperatingDayException(BaseModel):
    """Corresponds to Transmodel 'OperatingDay' exceptions / GTFS 'calendar_dates'."""
    day_type_id: str = Field(..., description="Reference to DayType.id")
    date: str = Field(..., description="YYYY-MM-DD")
    is_addition: bool = Field(..., description="True if adding service, False if removing")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class JourneyPattern(BaseModel):
    """Corresponds to Transmodel 'JourneyPattern' (defines the stop sequence)."""
    id: str = Field(..., description="Unique journey pattern identifier")
    line_id: str = Field(..., description="Reference to Line.id")
    direction: Optional[DirectionType] = Field(None)
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class PointInJourneyPattern(BaseModel):
    """Corresponds to Transmodel 'PointInJourneyPattern' (stop sequence mapping)."""
    journey_pattern_id: str = Field(..., description="Reference to JourneyPattern.id")
    stop_point_id: str = Field(..., description="Reference to ScheduledStopPoint.id")
    order: int = Field(..., description="0-indexed sequence order")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class ServiceJourney(BaseModel):
    """Corresponds to Transmodel 'ServiceJourney' / GTFS 'trip'."""
    id: str = Field(..., description="Unique service journey identifier")
    line_id: str = Field(..., description="Reference to Line.id")
    journey_pattern_id: Optional[str] = Field(None, description="Reference to JourneyPattern.id")
    day_type_id: str = Field(..., description="Reference to DayType.id")
    departure_time: str = Field(..., description="HH:MM:SS format")
    wheelchair_accessible: Optional[int] = Field(None, description="0=no info, 1=yes, 2=no")
    bikes_allowed: Optional[int] = Field(None, description="0=no info, 1=yes, 2=no")
    shape_id: Optional[str] = Field(None, description="Reference to shape geometry")
    headsign: Optional[str] = Field(None, description="Destination display text (GTFS trip_headsign)")
    short_name: Optional[str] = Field(None, description="Public trip identifier (GTFS trip_short_name)")
    block_id: Optional[str] = Field(None, description="Vehicle block for interlining (GTFS block_id)")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class PassingTime(BaseModel):
    """Corresponds to Transmodel 'PassingTime' / GTFS 'stop_times'."""
    service_journey_id: str = Field(..., description="Reference to ServiceJourney.id")
    stop_point_id: str = Field(..., description="Reference to ScheduledStopPoint.id")
    order: int = Field(..., description="Sequence order matching PointInJourneyPattern")
    arrival_time: Optional[str] = Field(None, description="HH:MM:SS format")
    departure_time: Optional[str] = Field(None, description="HH:MM:SS format")
    pickup_type: Optional[int] = Field(None, description="0/None=regular, 1=none, 2=phone, 3=driver")
    drop_off_type: Optional[int] = Field(None, description="0/None=regular, 1=none, 2=phone, 3=driver")
    timepoint: Optional[int] = Field(None, description="0=approximate, 1/None=exact")
    stop_headsign: Optional[str] = Field(None, description="Headsign override at this stop")
    shape_dist_traveled: Optional[float] = Field(None, description="Distance along shape")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

# ═══════════════════════════════════════════════════════════════════════════
# Station Topology
# ═══════════════════════════════════════════════════════════════════════════

class Level(BaseModel):
    """Multi-level station support / GTFS 'levels.txt'."""
    id: str = Field(..., description="Unique level identifier")
    index: float = Field(..., description="Numeric index, 0=ground, negative=below")
    name: Optional[str] = Field(None, description="Human-readable level name")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class StopArea(BaseModel):
    """Non-platform stop locations / GTFS stop with location_type >= 1."""
    id: str = Field(..., description="Unique stop area identifier")
    name: str = Field(..., description="Public name")
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)
    location_type: int = Field(1, description="1=station, 2=entrance, 3=node, 4=boarding_area")
    parent_id: Optional[str] = Field(None, description="Self-referencing parent stop area")
    level_id: Optional[str] = Field(None, description="Reference to Level.id")
    wheelchair_boarding: Optional[int] = Field(None, description="0=no info, 1=yes, 2=no")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class Pathway(BaseModel):
    """Station interior connectivity / GTFS 'pathways.txt'."""
    id: str = Field(..., description="Unique pathway identifier")
    from_stop_id: str = Field(..., description="Reference to stop or stop area")
    to_stop_id: str = Field(..., description="Reference to stop or stop area")
    pathway_mode: int = Field(..., description="1=walkway, 2=stairs, 3=travelator, 4=escalator, 5=elevator, 6=fare_gate, 7=exit_gate")
    is_bidirectional: bool = Field(...)
    length: Optional[float] = Field(None, description="Horizontal length in meters")
    traversal_time: Optional[int] = Field(None, description="Seconds to traverse")
    stair_count: Optional[int] = Field(None)
    max_slope: Optional[float] = Field(None)
    min_width: Optional[float] = Field(None, description="Minimum width in meters")
    signposted_as: Optional[str] = Field(None)
    reversed_signposted_as: Optional[str] = Field(None)
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

# ═══════════════════════════════════════════════════════════════════════════
# Supplementary Entities
# ═══════════════════════════════════════════════════════════════════════════

class FeedInfo(BaseModel):
    """Feed-level metadata / GTFS 'feed_info'."""
    id: str = Field(default="default_feed", description="Feed identifier")
    publisher_name: str = Field(..., description="Organization publishing the feed")
    publisher_url: str = Field(..., description="Publisher's URL")
    lang: str = Field(..., description="Default language (e.g., pt)")
    start_date: Optional[str] = Field(None, description="Feed validity start YYYYMMDD")
    end_date: Optional[str] = Field(None, description="Feed validity end YYYYMMDD")
    version: Optional[str] = Field(None, description="Feed version string")
    contact_email: Optional[str] = Field(None)
    contact_url: Optional[str] = Field(None)
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class ShapePoint(BaseModel):
    """A single point in a route shape / GTFS 'shapes'."""
    shape_id: str = Field(..., description="Shape group identifier")
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    sequence: int = Field(..., description="Order along the shape")
    dist_traveled: Optional[float] = Field(None, description="Cumulative distance in meters")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class Frequency(BaseModel):
    """Headway-based service / GTFS 'frequencies'."""
    service_journey_id: str = Field(..., description="Reference to ServiceJourney.id")
    start_time: str = Field(..., description="HH:MM:SS")
    end_time: str = Field(..., description="HH:MM:SS")
    headway_secs: int = Field(..., description="Seconds between departures")
    exact_times: Optional[int] = Field(0, description="0=frequency, 1=exact schedule")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class Transfer(BaseModel):
    """Stop-to-stop transfer rule / GTFS 'transfers'."""
    from_stop_id: str = Field(..., description="Reference to ScheduledStopPoint.id or StopArea.id")
    to_stop_id: str = Field(..., description="Reference to ScheduledStopPoint.id or StopArea.id")
    transfer_type: int = Field(0, description="0=recommended, 1=timed, 2=min_time, 3=none")
    min_transfer_time: Optional[int] = Field(None, description="Minimum transfer time in seconds")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class Translation(BaseModel):
    """Multi-language text / GTFS 'translations.txt'."""
    table_name: str = Field(..., description="GTFS table name (e.g., 'stops')")
    field_name: str = Field(..., description="GTFS field name (e.g., 'stop_name')")
    language: str = Field(..., description="BCP-47 language code")
    translation: str = Field(..., description="Translated text")
    record_id: str = Field(default="", description="ID of the record being translated")
    record_sub_id: str = Field(default="", description="Sub-record ID for compound keys")
    field_value: str = Field(default="", description="Value to match when record_id absent")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class FareAttribute(BaseModel):
    """Fare pricing / GTFS 'fare_attributes.txt'."""
    id: str = Field(..., description="Unique fare identifier")
    price: float = Field(..., description="Fare price")
    currency_type: str = Field(..., description="ISO 4217 currency code")
    payment_method: int = Field(..., description="0=on board, 1=before boarding")
    transfers: Optional[int] = Field(None, description="Number of transfers allowed (empty=unlimited)")
    operator_id: Optional[str] = Field(None, description="Reference to Operator.id")
    transfer_duration: Optional[int] = Field(None, description="Transfer window in seconds")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class FareRule(BaseModel):
    """Fare applicability rule / GTFS 'fare_rules.txt'."""
    fare_id: str = Field(..., description="Reference to FareAttribute.id")
    route_id: str = Field(default="", description="Reference to Line.id")
    origin_id: str = Field(default="", description="Origin zone ID")
    destination_id: str = Field(default="", description="Destination zone ID")
    contains_id: str = Field(default="", description="Zone the trip passes through")
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

class Attribution(BaseModel):
    """Dataset attribution / GTFS 'attributions.txt'."""
    id: str = Field(default="default_attribution", description="Attribution identifier")
    organization_name: str = Field(..., description="Organization name")
    is_producer: Optional[bool] = Field(None)
    is_operator: Optional[bool] = Field(None)
    is_authority: Optional[bool] = Field(None)
    attribution_url: Optional[str] = Field(None)
    attribution_email: Optional[str] = Field(None)
    attribution_phone: Optional[str] = Field(None)
    extras: Optional[str] = Field(None, description="JSON object of unmapped source fields")

# Registry of all core models for database iteration
TRANSMODEL_ENTITIES = {
    "operator": Operator,
    "line": Line,
    "scheduled_stop_point": ScheduledStopPoint,
    "level": Level,
    "stop_area": StopArea,
    "pathway": Pathway,
    "day_type": DayType,
    "operating_day_exception": OperatingDayException,
    "journey_pattern": JourneyPattern,
    "point_in_journey_pattern": PointInJourneyPattern,
    "service_journey": ServiceJourney,
    "passing_time": PassingTime,
    "feed_info": FeedInfo,
    "shape_point": ShapePoint,
    "frequency": Frequency,
    "transfer": Transfer,
    "fare_attribute": FareAttribute,
    "fare_rule": FareRule,
    "translation": Translation,
    "attribution": Attribution,
}
