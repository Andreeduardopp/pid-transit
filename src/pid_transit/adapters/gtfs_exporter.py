"""
GTFS Exporter Adapter (Object-Oriented).
"""

import csv
import io
import zipfile
import logging
from typing import Dict, Any, List, Union
from pathlib import Path

from ..core.database import TransmodelDatabase
from ..core.schemas import TransportMode

logger = logging.getLogger(__name__)

class GtfsExporter:
    """
    Adapter class for exporting TransmodelDatabase contents into a GTFS zip archive.
    """

    def __init__(self, include_shapes: bool = False):
        self.include_shapes = include_shapes

    def _mode_to_gtfs_route_type(self, mode: str) -> int:
        if mode == TransportMode.TRAM.value: return 0
        if mode == TransportMode.METRO.value: return 1
        if mode == TransportMode.RAIL.value: return 2
        if mode == TransportMode.BUS.value: return 3
        if mode == TransportMode.FERRY.value: return 4
        return 3

    def export_from_db(self, db: TransmodelDatabase, output_path: Union[Path, str]) -> None:
        """Export the database to a GTFS zip file."""
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            
            def write_csv(filename: str, rows: List[Dict[str, Any]]):
                if not rows:
                    return
                mem_file = io.StringIO()
                writer = csv.DictWriter(mem_file, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
                zf.writestr(filename, mem_file.getvalue())

            # 1. agency.txt
            operators = db.get_records("operator")
            agencies = []
            for op in operators:
                agencies.append({
                    "agency_id": op["id"],
                    "agency_name": op["name"],
                    "agency_url": op["url"] or "http://unknown.url",
                    "agency_timezone": op["timezone"],
                    "agency_lang": op.get("lang", ""),
                    "agency_phone": op.get("phone", "")
                })
            write_csv("agency.txt", agencies)

            # 2. routes.txt
            lines = db.get_records("line")
            routes = []
            for line in lines:
                routes.append({
                    "route_id": line["id"],
                    "agency_id": line.get("operator_id", ""),
                    "route_short_name": line.get("short_name", ""),
                    "route_long_name": line["name"],
                    "route_type": self._mode_to_gtfs_route_type(line["transport_mode"]),
                    "route_color": line.get("color", "")
                })
            write_csv("routes.txt", routes)

            # 3. stops.txt
            stop_points = db.get_records("scheduled_stop_point")
            stops = []
            for sp in stop_points:
                stops.append({
                    "stop_id": sp["id"],
                    "stop_name": sp["name"],
                    "stop_lat": sp["lat"],
                    "stop_lon": sp["lon"],
                    "location_type": 0,
                    "parent_station": sp.get("stop_area_id", ""),
                    "wheelchair_boarding": sp.get("wheelchair_boarding", "") if sp.get("wheelchair_boarding") is not None else "",
                })
            write_csv("stops.txt", stops)

            # 4. calendar.txt
            day_types = db.get_records("day_type")
            calendars = []
            for dt in day_types:
                calendars.append({
                    "service_id": dt["id"],
                    "monday": dt["monday"],
                    "tuesday": dt["tuesday"],
                    "wednesday": dt["wednesday"],
                    "thursday": dt["thursday"],
                    "friday": dt["friday"],
                    "saturday": dt["saturday"],
                    "sunday": dt["sunday"],
                    "start_date": dt["start_date"],
                    "end_date": dt["end_date"]
                })
            write_csv("calendar.txt", calendars)

            # 5. calendar_dates.txt
            exceptions = db.get_records("operating_day_exception")
            cal_dates = []
            for exc in exceptions:
                cal_dates.append({
                    "service_id": exc["day_type_id"],
                    "date": exc["date"],
                    "exception_type": 1 if exc["is_addition"] else 2
                })
            write_csv("calendar_dates.txt", cal_dates)

            # 6. trips.txt
            service_journeys = db.get_records("service_journey")
            trips = []
            for sj in service_journeys:
                trip = {
                    "route_id": sj["line_id"],
                    "service_id": sj["day_type_id"],
                    "trip_id": sj["id"],
                    "wheelchair_accessible": sj.get("wheelchair_accessible", "") if sj.get("wheelchair_accessible") is not None else "",
                    "bikes_allowed": sj.get("bikes_allowed", "") if sj.get("bikes_allowed") is not None else "",
                }
                if sj.get("shape_id"):
                    trip["shape_id"] = sj["shape_id"]
                trips.append(trip)
            write_csv("trips.txt", trips)

            # 7. stop_times.txt
            passing_times = db.get_records("passing_time")
            stop_times = []
            for pt in passing_times:
                stop_times.append({
                    "trip_id": pt["service_journey_id"],
                    "arrival_time": pt.get("arrival_time", ""),
                    "departure_time": pt.get("departure_time", ""),
                    "stop_id": pt["stop_point_id"],
                    "stop_sequence": pt["order"]
                })
            write_csv("stop_times.txt", stop_times)

            # 8. feed_info.txt
            feed_infos = db.get_records("feed_info")
            fi_rows = []
            for fi in feed_infos:
                fi_rows.append({
                    "feed_publisher_name": fi["publisher_name"],
                    "feed_publisher_url": fi["publisher_url"],
                    "feed_lang": fi["lang"],
                    "feed_start_date": fi.get("start_date", ""),
                    "feed_end_date": fi.get("end_date", ""),
                    "feed_version": fi.get("version", ""),
                    "feed_contact_email": fi.get("contact_email", ""),
                    "feed_contact_url": fi.get("contact_url", ""),
                })
            write_csv("feed_info.txt", fi_rows)

            # 9. shapes.txt
            if self.include_shapes:
                shape_points = db.get_records("shape_point")
                shapes = []
                for sp in shape_points:
                    row = {
                        "shape_id": sp["shape_id"],
                        "shape_pt_lat": sp["lat"],
                        "shape_pt_lon": sp["lon"],
                        "shape_pt_sequence": sp["sequence"],
                    }
                    if sp.get("dist_traveled") is not None:
                        row["shape_dist_traveled"] = sp["dist_traveled"]
                    shapes.append(row)
                write_csv("shapes.txt", shapes)

            # 10. frequencies.txt
            freq_records = db.get_records("frequency")
            freqs = []
            for fr in freq_records:
                freqs.append({
                    "trip_id": fr["service_journey_id"],
                    "start_time": fr["start_time"],
                    "end_time": fr["end_time"],
                    "headway_secs": fr["headway_secs"],
                    "exact_times": fr.get("exact_times", 0),
                })
            write_csv("frequencies.txt", freqs)

            # 11. transfers.txt
            transfer_records = db.get_records("transfer")
            transfers = []
            for tr in transfer_records:
                row = {
                    "from_stop_id": tr["from_stop_id"],
                    "to_stop_id": tr["to_stop_id"],
                    "transfer_type": tr["transfer_type"],
                }
                if tr.get("min_transfer_time") is not None:
                    row["min_transfer_time"] = tr["min_transfer_time"]
                transfers.append(row)
            write_csv("transfers.txt", transfers)
