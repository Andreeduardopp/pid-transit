"""
GTFS Importer Adapter (Object-Oriented).
"""

import csv
import io
import zipfile
import logging
from typing import Dict, Any, Union
from pathlib import Path

from ..core.database import TransmodelDatabase
from ..core.schemas import TransportMode, DirectionType

logger = logging.getLogger(__name__)

class GtfsImporter:
    """
    Adapter class for importing GTFS .zip files into the Transmodel database.
    """
    
    def __init__(self, strict_mode: bool = False, fallback_timezone: str = "UTC"):
        self.strict_mode = strict_mode
        self.fallback_timezone = fallback_timezone

    def _iter_csv_rows(self, zf: zipfile.ZipFile, filename: str):
        if filename not in zf.namelist():
            return
        with zf.open(filename, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text)
            for row in reader:
                cleaned: Dict[str, Any] = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    key = k.strip()
                    if not key:
                        continue
                    cleaned[key] = v.strip() if v is not None else ""
                yield cleaned

    def _gtfs_route_type_to_mode(self, route_type: int) -> TransportMode:
        rt = int(route_type)
        if rt == 0: return TransportMode.TRAM
        if rt == 1: return TransportMode.METRO
        if rt == 2: return TransportMode.RAIL
        if rt == 3: return TransportMode.BUS
        if rt == 4: return TransportMode.FERRY
        return TransportMode.OTHER

    def import_to_db(self, db: TransmodelDatabase, archive_path: Union[Path, str]) -> Dict[str, int]:
        """Import GTFS zip into the database."""
        stats = {}
        with zipfile.ZipFile(archive_path) as zf:
            
            # 1. Operators
            operators = []
            for row in self._iter_csv_rows(zf, "agency.txt"):
                operators.append({
                    "id": row.get("agency_id", "default_agency"),
                    "name": row.get("agency_name", "Unknown Agency"),
                    "url": row.get("agency_url"),
                    "timezone": row.get("agency_timezone", self.fallback_timezone),
                    "lang": row.get("agency_lang"),
                    "phone": row.get("agency_phone")
                })
            if operators:
                stats["operator"] = db.upsert("operator", operators)

            # 2. Lines
            lines = []
            for row in self._iter_csv_rows(zf, "routes.txt"):
                lines.append({
                    "id": row["route_id"],
                    "operator_id": row.get("agency_id") or (operators[0]["id"] if operators else None),
                    "name": row.get("route_long_name") or row.get("route_short_name") or "Unnamed",
                    "short_name": row.get("route_short_name"),
                    "transport_mode": self._gtfs_route_type_to_mode(row.get("route_type", 3)),
                    "color": row.get("route_color")
                })
            if lines:
                stats["line"] = db.upsert("line", lines)

            # 3. ScheduledStopPoints
            stops = []
            for row in self._iter_csv_rows(zf, "stops.txt"):
                if row.get("location_type") not in ("0", "", None):
                    continue
                stops.append({
                    "id": row["stop_id"],
                    "name": row.get("stop_name", "Unnamed Stop"),
                    "lat": float(row["stop_lat"]),
                    "lon": float(row["stop_lon"]),
                    "stop_area_id": row.get("parent_station")
                })
            if stops:
                stats["scheduled_stop_point"] = db.upsert("scheduled_stop_point", stops)

            # 4. DayTypes
            day_types = []
            for row in self._iter_csv_rows(zf, "calendar.txt"):
                day_types.append({
                    "id": row["service_id"],
                    "monday": row.get("monday") == "1",
                    "tuesday": row.get("tuesday") == "1",
                    "wednesday": row.get("wednesday") == "1",
                    "thursday": row.get("thursday") == "1",
                    "friday": row.get("friday") == "1",
                    "saturday": row.get("saturday") == "1",
                    "sunday": row.get("sunday") == "1",
                    "start_date": row["start_date"],
                    "end_date": row["end_date"]
                })
            if day_types:
                stats["day_type"] = db.upsert("day_type", day_types)

            # 5. OperatingDayExceptions
            exceptions = []
            for row in self._iter_csv_rows(zf, "calendar_dates.txt"):
                exceptions.append({
                    "day_type_id": row["service_id"],
                    "date": row["date"],
                    "is_addition": row.get("exception_type") == "1"
                })
            if exceptions:
                stats["operating_day_exception"] = db.upsert("operating_day_exception", exceptions)

            # 6 & 7. Trips and StopTimes
            journey_patterns = []
            service_journeys = []
            for row in self._iter_csv_rows(zf, "trips.txt"):
                jp_id = f"JP_{row['trip_id']}"
                direction = None
                if row.get("direction_id") == "0":
                    direction = DirectionType.OUTBOUND
                elif row.get("direction_id") == "1":
                    direction = DirectionType.INBOUND

                journey_patterns.append({
                    "id": jp_id,
                    "line_id": row["route_id"],
                    "direction": direction
                })

                service_journeys.append({
                    "id": row["trip_id"],
                    "line_id": row["route_id"],
                    "journey_pattern_id": jp_id,
                    "day_type_id": row["service_id"],
                    "departure_time": "00:00:00"
                })

            if journey_patterns:
                stats["journey_pattern"] = db.upsert("journey_pattern", journey_patterns)

            points_in_jp = []
            passing_times = []
            for row in self._iter_csv_rows(zf, "stop_times.txt"):
                trip_id = row["trip_id"]
                jp_id = f"JP_{trip_id}"
                
                points_in_jp.append({
                    "journey_pattern_id": jp_id,
                    "stop_point_id": row["stop_id"],
                    "order": int(row["stop_sequence"])
                })
                
                passing_times.append({
                    "service_journey_id": trip_id,
                    "stop_point_id": row["stop_id"],
                    "order": int(row["stop_sequence"]),
                    "arrival_time": row.get("arrival_time"),
                    "departure_time": row.get("departure_time")
                })

                if int(row["stop_sequence"]) in (0, 1) and row.get("departure_time"):
                    for sj in service_journeys:
                        if sj["id"] == trip_id:
                            sj["departure_time"] = row["departure_time"]

            if service_journeys:
                stats["service_journey"] = db.upsert("service_journey", service_journeys)
            if points_in_jp:
                stats["point_in_journey_pattern"] = db.upsert("point_in_journey_pattern", points_in_jp)
            if passing_times:
                stats["passing_time"] = db.upsert("passing_time", passing_times)

        return stats
