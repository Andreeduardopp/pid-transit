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
                wb = row.get("wheelchair_boarding")
                stops.append({
                    "id": row["stop_id"],
                    "name": row.get("stop_name", "Unnamed Stop"),
                    "lat": float(row["stop_lat"]),
                    "lon": float(row["stop_lon"]),
                    "stop_area_id": row.get("parent_station"),
                    "wheelchair_boarding": int(wb) if wb and wb.strip() else None,
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
                known_day_type_ids = {dt["id"] for dt in day_types}
                missing_ids = {e["day_type_id"] for e in exceptions} - known_day_type_ids
                if missing_ids:
                    dates = [e["date"] for e in exceptions if e["day_type_id"] in missing_ids]
                    min_date = min(dates) if dates else "19700101"
                    max_date = max(dates) if dates else "19700101"
                    placeholder_day_types = [{
                        "id": sid,
                        "monday": False, "tuesday": False, "wednesday": False,
                        "thursday": False, "friday": False, "saturday": False,
                        "sunday": False,
                        "start_date": min_date, "end_date": max_date,
                    } for sid in missing_ids]
                    stats["day_type"] = stats.get("day_type", 0) + db.upsert("day_type", placeholder_day_types)
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

                wa = row.get("wheelchair_accessible")
                ba = row.get("bikes_allowed")
                service_journeys.append({
                    "id": row["trip_id"],
                    "line_id": row["route_id"],
                    "journey_pattern_id": jp_id,
                    "day_type_id": row["service_id"],
                    "departure_time": "00:00:00",
                    "wheelchair_accessible": int(wa) if wa and wa.strip() else None,
                    "bikes_allowed": int(ba) if ba and ba.strip() else None,
                    "shape_id": row.get("shape_id") or None,
                })

            if journey_patterns:
                stats["journey_pattern"] = db.upsert("journey_pattern", journey_patterns)

            sj_index = {sj["id"]: i for i, sj in enumerate(service_journeys)}

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
                    idx = sj_index.get(trip_id)
                    if idx is not None:
                        service_journeys[idx]["departure_time"] = row["departure_time"]

            if service_journeys:
                stats["service_journey"] = db.upsert("service_journey", service_journeys)
            if points_in_jp:
                stats["point_in_journey_pattern"] = db.upsert("point_in_journey_pattern", points_in_jp)
            if passing_times:
                stats["passing_time"] = db.upsert("passing_time", passing_times)

            # 8. FeedInfo
            feed_infos = []
            for row in self._iter_csv_rows(zf, "feed_info.txt"):
                feed_infos.append({
                    "id": "default_feed",
                    "publisher_name": row.get("feed_publisher_name", "Unknown"),
                    "publisher_url": row.get("feed_publisher_url", ""),
                    "lang": row.get("feed_lang", "en"),
                    "start_date": row.get("feed_start_date"),
                    "end_date": row.get("feed_end_date"),
                    "version": row.get("feed_version"),
                    "contact_email": row.get("feed_contact_email"),
                    "contact_url": row.get("feed_contact_url"),
                })
            if feed_infos:
                stats["feed_info"] = db.upsert("feed_info", feed_infos)

            # 9. Shapes
            shape_points = []
            for row in self._iter_csv_rows(zf, "shapes.txt"):
                shape_points.append({
                    "shape_id": row["shape_id"],
                    "lat": float(row["shape_pt_lat"]),
                    "lon": float(row["shape_pt_lon"]),
                    "sequence": int(row["shape_pt_sequence"]),
                    "dist_traveled": float(row["shape_dist_traveled"]) if row.get("shape_dist_traveled") else None,
                })
            if shape_points:
                stats["shape_point"] = db.upsert("shape_point", shape_points)

            # 10. Frequencies
            frequencies = []
            for row in self._iter_csv_rows(zf, "frequencies.txt"):
                et = row.get("exact_times")
                frequencies.append({
                    "service_journey_id": row["trip_id"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "headway_secs": int(row["headway_secs"]),
                    "exact_times": int(et) if et and et.strip() else 0,
                })
            if frequencies:
                stats["frequency"] = db.upsert("frequency", frequencies)

            # 11. Transfers
            transfers = []
            for row in self._iter_csv_rows(zf, "transfers.txt"):
                tt = row.get("transfer_type", "0")
                mtt = row.get("min_transfer_time")
                transfers.append({
                    "from_stop_id": row["from_stop_id"],
                    "to_stop_id": row["to_stop_id"],
                    "transfer_type": int(tt) if tt and tt.strip() else 0,
                    "min_transfer_time": int(mtt) if mtt and mtt.strip() else None,
                })
            if transfers:
                stats["transfer"] = db.upsert("transfer", transfers)

        return stats
