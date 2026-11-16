"""
GTFS Exporter Adapter (Object-Oriented).
"""

import csv
import io
import json
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


def _apply_extras(row: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay a record's `extras` JSON onto the output row.

    Unmapped source columns reappear in the CSV, and pinned fields (e.g. the
    original route_type) override the value computed from the model.
    """
    extras = record.get("extras")
    if extras:
        try:
            row.update(json.loads(extras))
        except (ValueError, TypeError):
            logger.warning("Ignoring malformed extras on %s record",
                           record.get("id", "?"))
    return row


class GtfsExporter:
    """
    Adapter class for exporting TransmodelDatabase contents into a GTFS zip archive.
    """

    def __init__(self, include_shapes: bool = True):
        self.include_shapes = include_shapes

    _MODE_TO_ROUTE_TYPE = {
        TransportMode.TRAM.value: 0,
        TransportMode.METRO.value: 1,
        TransportMode.RAIL.value: 2,
        TransportMode.BUS.value: 3,
        TransportMode.FERRY.value: 4,
        TransportMode.CABLE_CAR.value: 5,
        TransportMode.GONDOLA.value: 6,
        TransportMode.FUNICULAR.value: 7,
        TransportMode.TROLLEYBUS.value: 11,
        TransportMode.MONORAIL.value: 12,
    }

    def _mode_to_gtfs_route_type(self, mode: str) -> int:
        return self._MODE_TO_ROUTE_TYPE.get(mode, 3)

    def export_from_db(self, db: TransmodelDatabase, output_path: Union[Path, str]) -> None:
        """Export the database to a GTFS zip file."""
        logger.info("Exporting GTFS to %s", output_path)
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:

            def write_csv(filename: str, rows: List[Dict[str, Any]]):
                if not rows:
                    return
                logger.info("Writing %s (%d records)", filename, len(rows))
                # Union of keys across all rows (extras overlays can add
                # columns that only some rows carry); rows missing a key get "".
                fieldnames = list(dict.fromkeys(k for row in rows for k in row))
                mem_file = io.StringIO()
                writer = csv.DictWriter(mem_file, fieldnames=fieldnames, restval="")
                writer.writeheader()
                writer.writerows(rows)
                zf.writestr(filename, mem_file.getvalue())

            # 1. agency.txt
            operators = db.get_records("operator")
            agencies = []
            for op in operators:
                agencies.append(_apply_extras({
                    "agency_id": op["id"],
                    "agency_name": op["name"],
                    "agency_url": op["url"] or "https://example.com",
                    "agency_timezone": op["timezone"],
                    "agency_lang": _v(op.get("lang")),
                    "agency_phone": _v(op.get("phone")),
                }, op))
            write_csv("agency.txt", agencies)

            # 2. routes.txt
            lines = db.get_records("line")
            routes = []
            for line in lines:
                # The extras overlay restores the original route_type when the
                # importer pinned it (exact round-trip for extended types).
                routes.append(_apply_extras({
                    "route_id": line["id"],
                    "agency_id": _v(line.get("operator_id")),
                    "route_short_name": _v(line.get("short_name")),
                    "route_long_name": line["name"],
                    "route_type": self._mode_to_gtfs_route_type(line["transport_mode"]),
                    "route_color": _v(line.get("color")),
                    "route_text_color": _v(line.get("text_color")),
                    "route_desc": _v(line.get("description")),
                    "route_url": _v(line.get("url")),
                    "route_sort_order": _v(line.get("sort_order")),
                }, line))
            write_csv("routes.txt", routes)

            # 3. levels.txt
            level_records = db.get_records("level")
            levels_rows = []
            for lv in level_records:
                levels_rows.append(_apply_extras({
                    "level_id": lv["id"],
                    "level_index": lv["index"],
                    "level_name": _v(lv.get("name")),
                }, lv))
            write_csv("levels.txt", levels_rows)

            # 4. stops.txt (platforms + stop areas combined)
            stop_points = db.get_records("scheduled_stop_point")
            stop_area_records = db.get_records("stop_area")
            stops = []
            for sp in stop_points:
                stops.append(_apply_extras({
                    "stop_id": sp["id"],
                    "stop_name": sp["name"],
                    "stop_lat": _v(sp.get("lat")),
                    "stop_lon": _v(sp.get("lon")),
                    "location_type": 0,
                    "parent_station": _v(sp.get("stop_area_id")),
                    "wheelchair_boarding": _v(sp.get("wheelchair_boarding")),
                    "level_id": "",
                    "stop_code": _v(sp.get("code")),
                    "platform_code": _v(sp.get("platform_code")),
                    "zone_id": _v(sp.get("zone_id")),
                    "stop_desc": _v(sp.get("description")),
                }, sp))
            for sa in stop_area_records:
                stops.append(_apply_extras({
                    "stop_id": sa["id"],
                    "stop_name": sa["name"],
                    "stop_lat": _v(sa.get("lat")),
                    "stop_lon": _v(sa.get("lon")),
                    "location_type": sa["location_type"],
                    "parent_station": _v(sa.get("parent_id")),
                    "wheelchair_boarding": _v(sa.get("wheelchair_boarding")),
                    "level_id": _v(sa.get("level_id")),
                }, sa))
            write_csv("stops.txt", stops)

            # 5. pathways.txt
            pathway_records = db.get_records("pathway")
            pathways = []
            for pw in pathway_records:
                pathways.append(_apply_extras({
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
                }, pw))
            write_csv("pathways.txt", pathways)

            # 5. calendar_dates.txt -- read before calendar.txt, which needs to
            # know which services their exception dates already describe.
            exceptions = db.get_records("operating_day_exception")
            cal_dates = []
            added_on_some_date = set()
            for exc in exceptions:
                if exc["is_addition"]:
                    added_on_some_date.add(exc["day_type_id"])
                cal_dates.append(_apply_extras({
                    "service_id": exc["day_type_id"],
                    "date": exc["date"],
                    "exception_type": 1 if exc["is_addition"] else 2
                }, exc))
            write_csv("calendar_dates.txt", cal_dates)

            # 6. calendar.txt
            #
            # A service whose seven weekday flags are all false runs on no
            # weekday: the dates it runs are exactly the ones calendar_dates.txt
            # adds, and the row says nothing the exceptions do not already say.
            # GTFS makes calendar.txt conditionally required for this reason --
            # a feed may leave it out when calendar_dates.txt describes every
            # service -- and the Porto feed does leave it out. Writing the row
            # back is therefore not fidelity but an addition, and one the
            # MobilityData validator flags: an all-false row is usually a
            # mistake, so it raised service_has_no_active_day_of_the_week three
            # times, and a fourth notice because a non-ASCII service_id then
            # appeared in one more file. Four warnings, 5 603 against the
            # original's 5 599, for three rows carrying no information.
            #
            # The rule is deliberately narrow: all seven false *and* at least
            # one date added by an exception. An all-false service with no
            # addition runs never, and its row is the only place that says so,
            # so it is still written.
            day_types = db.get_records("day_type")
            calendars = []
            for dt in day_types:
                week = (dt["monday"], dt["tuesday"], dt["wednesday"],
                        dt["thursday"], dt["friday"], dt["saturday"],
                        dt["sunday"])
                if not any(week) and dt["id"] in added_on_some_date:
                    continue
                calendars.append(_apply_extras({
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
                }, dt))
            write_csv("calendar.txt", calendars)

            # 6. trips.txt
            service_journeys = db.get_records("service_journey")
            # direction_id lives on the journey pattern, not the journey
            jp_direction = {
                jp["id"]: jp.get("direction")
                for jp in db.get_records("journey_pattern")
            }
            _DIRECTION_ID = {"outbound": "0", "inbound": "1"}
            trips = []
            for sj in service_journeys:
                direction = jp_direction.get(sj.get("journey_pattern_id"))
                trips.append(_apply_extras({
                    "route_id": sj["line_id"],
                    "service_id": sj["day_type_id"],
                    "trip_id": sj["id"],
                    "trip_headsign": _v(sj.get("headsign")),
                    "trip_short_name": _v(sj.get("short_name")),
                    "direction_id": _DIRECTION_ID.get(direction, ""),
                    "block_id": _v(sj.get("block_id")),
                    "wheelchair_accessible": _v(sj.get("wheelchair_accessible")),
                    "bikes_allowed": _v(sj.get("bikes_allowed")),
                    "shape_id": _v(sj.get("shape_id")),
                }, sj))
            write_csv("trips.txt", trips)

            # 7. stop_times.txt
            passing_times = db.get_records("passing_time")
            stop_times = []
            for pt in passing_times:
                # GTFS wants both times whenever either is set (trip edges must
                # be fully timed); NeTEx calls often carry only a departure at
                # the first stop and only an arrival at the last.
                arr = pt.get("arrival_time") or pt.get("departure_time")
                dep = pt.get("departure_time") or pt.get("arrival_time")
                stop_times.append(_apply_extras({
                    "trip_id": pt["service_journey_id"],
                    "arrival_time": _v(arr),
                    "departure_time": _v(dep),
                    "stop_id": pt["stop_point_id"],
                    "stop_sequence": pt["order"],
                    "stop_headsign": _v(pt.get("stop_headsign")),
                    "pickup_type": _v(pt.get("pickup_type")),
                    "drop_off_type": _v(pt.get("drop_off_type")),
                    "timepoint": _v(pt.get("timepoint")),
                    "shape_dist_traveled": _v(pt.get("shape_dist_traveled")),
                }, pt))
            write_csv("stop_times.txt", stop_times)

            # 8. feed_info.txt
            feed_infos = db.get_records("feed_info")
            fi_rows = []
            for fi in feed_infos:
                fi_rows.append(_apply_extras({
                    "feed_publisher_name": fi["publisher_name"],
                    "feed_publisher_url": fi["publisher_url"],
                    "feed_lang": fi["lang"],
                    "feed_start_date": _v(fi.get("start_date")),
                    "feed_end_date": _v(fi.get("end_date")),
                    "feed_version": _v(fi.get("version")),
                    "feed_contact_email": _v(fi.get("contact_email")),
                    "feed_contact_url": _v(fi.get("contact_url")),
                }, fi))
            write_csv("feed_info.txt", fi_rows)

            # 9. shapes.txt
            if self.include_shapes:
                shape_points = db.get_records("shape_point")
                shapes = []
                for sp in shape_points:
                    shapes.append(_apply_extras({
                        "shape_id": sp["shape_id"],
                        "shape_pt_lat": sp["lat"],
                        "shape_pt_lon": sp["lon"],
                        "shape_pt_sequence": sp["sequence"],
                        "shape_dist_traveled": _v(sp.get("dist_traveled")),
                    }, sp))
                write_csv("shapes.txt", shapes)

            # 10. frequencies.txt
            freq_records = db.get_records("frequency")
            freqs = []
            for fr in freq_records:
                freqs.append(_apply_extras({
                    "trip_id": fr["service_journey_id"],
                    "start_time": fr["start_time"],
                    "end_time": fr["end_time"],
                    "headway_secs": fr["headway_secs"],
                    "exact_times": fr.get("exact_times", 0),
                }, fr))
            write_csv("frequencies.txt", freqs)

            # 11. transfers.txt
            transfer_records = db.get_records("transfer")
            transfers = []
            for tr in transfer_records:
                transfers.append(_apply_extras({
                    "from_stop_id": tr["from_stop_id"],
                    "to_stop_id": tr["to_stop_id"],
                    "transfer_type": tr["transfer_type"],
                    "min_transfer_time": _v(tr.get("min_transfer_time")),
                }, tr))
            write_csv("transfers.txt", transfers)

            # 12. fare_attributes.txt
            fare_attr_records = db.get_records("fare_attribute")
            fa_rows = []
            for fa in fare_attr_records:
                fa_rows.append(_apply_extras({
                    "fare_id": fa["id"],
                    "price": fa["price"],
                    "currency_type": fa["currency_type"],
                    "payment_method": fa["payment_method"],
                    "transfers": _v(fa.get("transfers")),
                    "agency_id": _v(fa.get("operator_id")),
                    "transfer_duration": _v(fa.get("transfer_duration")),
                }, fa))
            write_csv("fare_attributes.txt", fa_rows)

            # 13. fare_rules.txt
            fare_rule_records = db.get_records("fare_rule")
            fr_rows = []
            for fr in fare_rule_records:
                fr_rows.append(_apply_extras({
                    "fare_id": fr["fare_id"],
                    "route_id": _v(fr.get("route_id")),
                    "origin_id": _v(fr.get("origin_id")),
                    "destination_id": _v(fr.get("destination_id")),
                    "contains_id": _v(fr.get("contains_id")),
                }, fr))
            write_csv("fare_rules.txt", fr_rows)

            # 14. translations.txt
            translation_records = db.get_records("translation")
            trans_rows = []
            for tr in translation_records:
                trans_rows.append(_apply_extras({
                    "table_name": tr["table_name"],
                    "field_name": tr["field_name"],
                    "language": tr["language"],
                    "translation": tr["translation"],
                    "record_id": _v(tr.get("record_id")),
                    "record_sub_id": _v(tr.get("record_sub_id")),
                    "field_value": _v(tr.get("field_value")),
                }, tr))
            write_csv("translations.txt", trans_rows)

            # 13. attributions.txt
            attribution_records = db.get_records("attribution")
            attr_rows = []
            for at in attribution_records:
                attr_rows.append(_apply_extras({
                    "attribution_id": at["id"],
                    "organization_name": at["organization_name"],
                    "is_producer": "1" if at.get("is_producer") else ("0" if at.get("is_producer") is not None else ""),
                    "is_operator": "1" if at.get("is_operator") else ("0" if at.get("is_operator") is not None else ""),
                    "is_authority": "1" if at.get("is_authority") else ("0" if at.get("is_authority") is not None else ""),
                    "attribution_url": _v(at.get("attribution_url")),
                    "attribution_email": _v(at.get("attribution_email")),
                    "attribution_phone": _v(at.get("attribution_phone")),
                }, at))
            write_csv("attributions.txt", attr_rows)
