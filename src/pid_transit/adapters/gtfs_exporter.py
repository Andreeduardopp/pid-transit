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


def _v(val) -> str:
    """Convert None to empty string for GTFS CSV output."""
    return "" if val is None else str(val)


class GtfsExporter:
    """
    Adapter class for exporting TransmodelDatabase contents into a GTFS zip archive.
    """

    def __init__(self, include_shapes: bool = True):
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
        logger.info("Exporting GTFS to %s", output_path)
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:

            def write_csv(filename: str, rows: List[Dict[str, Any]]):
                if not rows:
                    return
                logger.info("Writing %s (%d records)", filename, len(rows))
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
                    "agency_lang": _v(op.get("lang")),
                    "agency_phone": _v(op.get("phone")),
                })
            write_csv("agency.txt", agencies)

            # 2. routes.txt
            lines = db.get_records("line")
            routes = []
            for line in lines:
                routes.append({
                    "route_id": line["id"],
                    "agency_id": _v(line.get("operator_id")),
                    "route_short_name": _v(line.get("short_name")),
                    "route_long_name": line["name"],
                    "route_type": self._mode_to_gtfs_route_type(line["transport_mode"]),
                    "route_color": _v(line.get("color")),
                })
            write_csv("routes.txt", routes)

            # 3. levels.txt
            level_records = db.get_records("level")
            levels_rows = []
            for lv in level_records:
                levels_rows.append({
                    "level_id": lv["id"],
                    "level_index": lv["index"],
                    "level_name": _v(lv.get("name")),
                })
            write_csv("levels.txt", levels_rows)

            # 4. stops.txt (platforms + stop areas combined)
            stop_points = db.get_records("scheduled_stop_point")
            stop_area_records = db.get_records("stop_area")
            stops = []
            for sp in stop_points:
                stops.append({
                    "stop_id": sp["id"],
                    "stop_name": sp["name"],
                    "stop_lat": sp["lat"],
                    "stop_lon": sp["lon"],
                    "location_type": 0,
                    "parent_station": _v(sp.get("stop_area_id")),
                    "wheelchair_boarding": _v(sp.get("wheelchair_boarding")),
                    "level_id": "",
                })
            for sa in stop_area_records:
                stops.append({
                    "stop_id": sa["id"],
                    "stop_name": sa["name"],
                    "stop_lat": _v(sa.get("lat")),
                    "stop_lon": _v(sa.get("lon")),
                    "location_type": sa["location_type"],
                    "parent_station": _v(sa.get("parent_id")),
                    "wheelchair_boarding": _v(sa.get("wheelchair_boarding")),
                    "level_id": _v(sa.get("level_id")),
                })
            write_csv("stops.txt", stops)

            # 5. pathways.txt
            pathway_records = db.get_records("pathway")
            pathways = []
            for pw in pathway_records:
                pathways.append({
                    "pathway_id": pw["id"],
                    "from_stop_id": pw["from_stop_id"],
                    "to_stop_id": pw["to_stop_id"],
                    "pathway_mode": pw["pathway_mode"],
                    "is_bidirectional": 1 if pw["is_bidirectional"] else 0,
                    "length": _v(pw.get("length")),
                    "traversal_time": _v(pw.get("traversal_time")),
                    "stair_count": _v(pw.get("stair_count")),
                    "max_slope": _v(pw.get("max_slope")),
                    "min_width": _v(pw.get("min_width")),
                    "signposted_as": _v(pw.get("signposted_as")),
                    "reversed_signposted_as": _v(pw.get("reversed_signposted_as")),
                })
            write_csv("pathways.txt", pathways)

            # 6. calendar.txt
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
                trips.append({
                    "route_id": sj["line_id"],
                    "service_id": sj["day_type_id"],
                    "trip_id": sj["id"],
                    "wheelchair_accessible": _v(sj.get("wheelchair_accessible")),
                    "bikes_allowed": _v(sj.get("bikes_allowed")),
                    "shape_id": _v(sj.get("shape_id")),
                })
            write_csv("trips.txt", trips)

            # 7. stop_times.txt
            passing_times = db.get_records("passing_time")
            stop_times = []
            for pt in passing_times:
                stop_times.append({
                    "trip_id": pt["service_journey_id"],
                    "arrival_time": _v(pt.get("arrival_time")),
                    "departure_time": _v(pt.get("departure_time")),
                    "stop_id": pt["stop_point_id"],
                    "stop_sequence": pt["order"],
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
                    "feed_start_date": _v(fi.get("start_date")),
                    "feed_end_date": _v(fi.get("end_date")),
                    "feed_version": _v(fi.get("version")),
                    "feed_contact_email": _v(fi.get("contact_email")),
                    "feed_contact_url": _v(fi.get("contact_url")),
                })
            write_csv("feed_info.txt", fi_rows)

            # 9. shapes.txt
            if self.include_shapes:
                shape_points = db.get_records("shape_point")
                shapes = []
                for sp in shape_points:
                    shapes.append({
                        "shape_id": sp["shape_id"],
                        "shape_pt_lat": sp["lat"],
                        "shape_pt_lon": sp["lon"],
                        "shape_pt_sequence": sp["sequence"],
                        "shape_dist_traveled": _v(sp.get("dist_traveled")),
                    })
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
                transfers.append({
                    "from_stop_id": tr["from_stop_id"],
                    "to_stop_id": tr["to_stop_id"],
                    "transfer_type": tr["transfer_type"],
                    "min_transfer_time": _v(tr.get("min_transfer_time")),
                })
            write_csv("transfers.txt", transfers)

            # 12. fare_attributes.txt
            fare_attr_records = db.get_records("fare_attribute")
            fa_rows = []
            for fa in fare_attr_records:
                fa_rows.append({
                    "fare_id": fa["id"],
                    "price": fa["price"],
                    "currency_type": fa["currency_type"],
                    "payment_method": fa["payment_method"],
                    "transfers": _v(fa.get("transfers")),
                    "agency_id": _v(fa.get("operator_id")),
                    "transfer_duration": _v(fa.get("transfer_duration")),
                })
            write_csv("fare_attributes.txt", fa_rows)

            # 13. fare_rules.txt
            fare_rule_records = db.get_records("fare_rule")
            fr_rows = []
            for fr in fare_rule_records:
                fr_rows.append({
                    "fare_id": fr["fare_id"],
                    "route_id": _v(fr.get("route_id")),
                    "origin_id": _v(fr.get("origin_id")),
                    "destination_id": _v(fr.get("destination_id")),
                    "contains_id": _v(fr.get("contains_id")),
                })
            write_csv("fare_rules.txt", fr_rows)

            # 14. translations.txt
            translation_records = db.get_records("translation")
            trans_rows = []
            for tr in translation_records:
                trans_rows.append({
                    "table_name": tr["table_name"],
                    "field_name": tr["field_name"],
                    "language": tr["language"],
                    "translation": tr["translation"],
                    "record_id": _v(tr.get("record_id")),
                    "record_sub_id": _v(tr.get("record_sub_id")),
                    "field_value": _v(tr.get("field_value")),
                })
            write_csv("translations.txt", trans_rows)

            # 13. attributions.txt
            attribution_records = db.get_records("attribution")
            attr_rows = []
            for at in attribution_records:
                attr_rows.append({
                    "attribution_id": at["id"],
                    "organization_name": at["organization_name"],
                    "is_producer": "1" if at.get("is_producer") else ("0" if at.get("is_producer") is not None else ""),
                    "is_operator": "1" if at.get("is_operator") else ("0" if at.get("is_operator") is not None else ""),
                    "is_authority": "1" if at.get("is_authority") else ("0" if at.get("is_authority") is not None else ""),
                    "attribution_url": _v(at.get("attribution_url")),
                    "attribution_email": _v(at.get("attribution_email")),
                    "attribution_phone": _v(at.get("attribution_phone")),
                })
            write_csv("attributions.txt", attr_rows)
