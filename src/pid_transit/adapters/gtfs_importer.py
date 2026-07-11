"""
GTFS Importer Adapter (Object-Oriented).
"""

import csv
import io
import json
import zipfile
import logging
from typing import Dict, Any, Optional, Union
from pathlib import Path

from ..core.database import TransmodelDatabase
from ..core.exceptions import ImportCancelledError
from ..core.schemas import TransportMode, DirectionType

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000


def _safe_float(val: str, field: str, row_id: str) -> Union[float, None]:
    if val is None or val.strip() == "":
        logger.warning("Empty %s for %s, skipping record", field, row_id)
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        logger.warning("Invalid %s '%s' for %s, skipping record", field, val, row_id)
        return None


def _safe_int(val: str, default: int = 0) -> int:
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _opt_int(val: Optional[str]) -> Optional[int]:
    """Empty/absent -> None, otherwise int (None on garbage)."""
    if val is None or val.strip() == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _opt_float(val: Optional[str]) -> Optional[float]:
    """Empty/absent -> None, otherwise float (None on garbage)."""
    if val is None or val.strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extras_json(row: Dict[str, str], consumed: frozenset,
                 force: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Serialize source columns not mapped to a model field as sorted JSON.

    Empty values are dropped (an absent column and an empty cell both
    round-trip to "not present"). `force` pins mapped-but-lossy fields
    (e.g. the original route_type) so exports can reproduce them exactly.
    """
    extra = {k: v for k, v in row.items() if k not in consumed and v != ""}
    if force:
        extra.update(force)
    if not extra:
        return None
    return json.dumps(extra, sort_keys=True, ensure_ascii=False)


# Columns each GTFS file maps onto model fields; everything else goes to extras.
_CONSUMED = {
    "agency": frozenset({"agency_id", "agency_name", "agency_url",
                         "agency_timezone", "agency_lang", "agency_phone"}),
    "routes": frozenset({"route_id", "agency_id", "route_short_name",
                         "route_long_name", "route_type", "route_color",
                         "route_text_color", "route_desc", "route_url",
                         "route_sort_order"}),
    "levels": frozenset({"level_id", "level_index", "level_name"}),
    "stops_point": frozenset({"stop_id", "stop_name", "stop_lat", "stop_lon",
                              "location_type", "parent_station",
                              "wheelchair_boarding", "stop_code",
                              "platform_code", "zone_id", "stop_desc"}),
    "stops_area": frozenset({"stop_id", "stop_name", "stop_lat", "stop_lon",
                             "location_type", "parent_station", "level_id",
                             "wheelchair_boarding"}),
    "pathways": frozenset({"pathway_id", "from_stop_id", "to_stop_id",
                           "pathway_mode", "is_bidirectional", "length",
                           "traversal_time", "stair_count", "max_slope",
                           "min_width", "signposted_as",
                           "reversed_signposted_as"}),
    "calendar": frozenset({"service_id", "monday", "tuesday", "wednesday",
                           "thursday", "friday", "saturday", "sunday",
                           "start_date", "end_date"}),
    "calendar_dates": frozenset({"service_id", "date", "exception_type"}),
    "trips": frozenset({"trip_id", "route_id", "service_id", "direction_id",
                        "wheelchair_accessible", "bikes_allowed", "shape_id",
                        "trip_headsign", "trip_short_name", "block_id"}),
    "stop_times": frozenset({"trip_id", "stop_id", "stop_sequence",
                             "arrival_time", "departure_time", "pickup_type",
                             "drop_off_type", "timepoint", "stop_headsign",
                             "shape_dist_traveled"}),
    "feed_info": frozenset({"feed_publisher_name", "feed_publisher_url",
                            "feed_lang", "feed_start_date", "feed_end_date",
                            "feed_version", "feed_contact_email",
                            "feed_contact_url"}),
    "shapes": frozenset({"shape_id", "shape_pt_lat", "shape_pt_lon",
                         "shape_pt_sequence", "shape_dist_traveled"}),
    "frequencies": frozenset({"trip_id", "start_time", "end_time",
                              "headway_secs", "exact_times"}),
    "transfers": frozenset({"from_stop_id", "to_stop_id", "transfer_type",
                            "min_transfer_time"}),
    "fare_attributes": frozenset({"fare_id", "price", "currency_type",
                                  "payment_method", "transfers", "agency_id",
                                  "transfer_duration"}),
    "fare_rules": frozenset({"fare_id", "route_id", "origin_id",
                             "destination_id", "contains_id"}),
    "translations": frozenset({"table_name", "field_name", "language",
                               "translation", "record_id", "record_sub_id",
                               "field_value"}),
    "attributions": frozenset({"attribution_id", "organization_name",
                               "is_producer", "is_operator", "is_authority",
                               "attribution_url", "attribution_email",
                               "attribution_phone"}),
}


class GtfsImporter:
    """
    Adapter class for importing GTFS .zip files into the Transmodel database.
    """
    
    def __init__(self, strict_mode: bool = False, fallback_timezone: str = "UTC",
                 on_progress=None, cancel_event=None):
        """
        Args:
            strict_mode: reserved for stricter validation behavior.
            fallback_timezone: used when agency.txt has no timezone.
            on_progress: optional callable
                ``(entity_type: str, count: int, *, file_index=None, total_files=None)``
                fired after every batch flush with the cumulative row count.
            cancel_event: optional ``threading.Event``; when set, the import
                aborts between batches with ``ImportCancelledError``.
        """
        self.strict_mode = strict_mode
        self.fallback_timezone = fallback_timezone
        self.on_progress = on_progress
        self.cancel_event = cancel_event

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ImportCancelledError("GTFS import cancelled")

    def _flush_table(self, db: TransmodelDatabase, stats: Dict[str, int],
                     table: str, rows: list) -> None:
        """Upsert rows in batches, reporting cumulative progress per flush."""
        for i in range(0, len(rows), BATCH_SIZE):
            self._check_cancel()
            n = db.upsert(table, rows[i:i + BATCH_SIZE])
            stats[table] = stats.get(table, 0) + n
            if self.on_progress is not None:
                self.on_progress(table, stats[table], file_index=None, total_files=None)

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

    def _gtfs_route_type_to_mode(self, route_type) -> TransportMode:
        rt = _safe_int(str(route_type), default=3)
        _BASIC = {0: TransportMode.TRAM, 1: TransportMode.METRO,
                  2: TransportMode.RAIL, 3: TransportMode.BUS,
                  4: TransportMode.FERRY, 5: TransportMode.CABLE_CAR,
                  6: TransportMode.GONDOLA, 7: TransportMode.FUNICULAR,
                  11: TransportMode.TROLLEYBUS, 12: TransportMode.MONORAIL}
        if rt in _BASIC:
            return _BASIC[rt]
        # Extended route types (GTFS-extensions / Google Transit)
        if 100 <= rt <= 199: return TransportMode.RAIL
        if 200 <= rt <= 299: return TransportMode.BUS   # coach
        if 400 <= rt <= 499: return TransportMode.METRO
        if 700 <= rt <= 799: return TransportMode.BUS
        if 800 <= rt <= 899: return TransportMode.TROLLEYBUS
        if 900 <= rt <= 999: return TransportMode.TRAM
        if 1000 <= rt <= 1099: return TransportMode.FERRY
        if 1300 <= rt <= 1399: return TransportMode.GONDOLA
        if 1400 <= rt <= 1499: return TransportMode.FUNICULAR
        logger.info("Unmapped route_type %d, defaulting to OTHER", rt)
        return TransportMode.OTHER

    def import_to_db(self, db: TransmodelDatabase, archive_path: Union[Path, str]) -> Dict[str, int]:
        """Import GTFS zip into the database."""
        logger.info("Importing GTFS archive: %s", archive_path)
        stats = {}
        warnings = 0
        with db.bulk_session(), zipfile.ZipFile(archive_path) as zf:

            # 1. Operators
            operators = []
            for row in self._iter_csv_rows(zf, "agency.txt"):
                operators.append({
                    "id": row.get("agency_id") or "default_agency",
                    "name": row.get("agency_name") or "Unknown Agency",
                    "url": row.get("agency_url") or None,
                    "timezone": row.get("agency_timezone") or self.fallback_timezone,
                    "lang": row.get("agency_lang") or None,
                    "phone": row.get("agency_phone") or None,
                    "extras": _extras_json(row, _CONSUMED["agency"]),
                })
            if operators:
                self._flush_table(db, stats, "operator", operators)
                logger.info("Imported %d operators", stats["operator"])
            else:
                logger.warning("No agency.txt or no agencies found; operator references will be NULL")
                warnings += 1

            default_op_id = operators[0]["id"] if operators else None

            # 2. Lines
            lines = []
            for row in self._iter_csv_rows(zf, "routes.txt"):
                # The original route_type is pinned in extras so the exporter
                # can reproduce it exactly (extended types would otherwise
                # collapse to their basic mode).
                route_type = str(_safe_int(row.get("route_type"), default=3))
                lines.append({
                    "id": row["route_id"],
                    "operator_id": row.get("agency_id") or default_op_id,
                    "name": row.get("route_long_name") or row.get("route_short_name") or "Unnamed",
                    "short_name": row.get("route_short_name") or None,
                    "transport_mode": self._gtfs_route_type_to_mode(route_type),
                    "color": row.get("route_color") or None,
                    "text_color": row.get("route_text_color") or None,
                    "description": row.get("route_desc") or None,
                    "url": row.get("route_url") or None,
                    "sort_order": _opt_int(row.get("route_sort_order")),
                    "extras": _extras_json(row, _CONSUMED["routes"],
                                           force={"route_type": route_type}),
                })
            if lines:
                self._flush_table(db, stats, "line", lines)
                logger.info("Imported %d lines", stats["line"])

            # 3. Levels
            levels = []
            for row in self._iter_csv_rows(zf, "levels.txt"):
                idx = _safe_float(row.get("level_index", "0"), "level_index", row.get("level_id", ""))
                levels.append({
                    "id": row["level_id"],
                    "index": idx if idx is not None else 0.0,
                    "name": row.get("level_name") or None,
                    "extras": _extras_json(row, _CONSUMED["levels"]),
                })
            if levels:
                self._flush_table(db, stats, "level", levels)
                logger.info("Imported %d levels", stats["level"])

            # 4. Stops (ScheduledStopPoints + StopAreas)
            stops = []
            stop_areas = []
            for row in self._iter_csv_rows(zf, "stops.txt"):
                stop_id = row.get("stop_id", "")
                loc_type = row.get("location_type", "0") or "0"
                lat = _safe_float(row.get("stop_lat", ""), "stop_lat", stop_id)
                lon = _safe_float(row.get("stop_lon", ""), "stop_lon", stop_id)
                wb = row.get("wheelchair_boarding")
                wb_val = _safe_int(wb) if wb and wb.strip() else None

                if loc_type == "0":
                    if lat is None or lon is None:
                        warnings += 1
                        continue
                    stops.append({
                        "id": stop_id,
                        "name": row.get("stop_name") or "Unnamed Stop",
                        "lat": lat,
                        "lon": lon,
                        "stop_area_id": row.get("parent_station") or None,
                        "wheelchair_boarding": wb_val,
                        "code": row.get("stop_code") or None,
                        "platform_code": row.get("platform_code") or None,
                        "zone_id": row.get("zone_id") or None,
                        "description": row.get("stop_desc") or None,
                        "extras": _extras_json(row, _CONSUMED["stops_point"]),
                    })
                elif loc_type in ("1", "2", "3", "4"):
                    stop_areas.append({
                        "id": stop_id,
                        "name": row.get("stop_name") or "Unnamed Stop Area",
                        "lat": lat,
                        "lon": lon,
                        "location_type": int(loc_type),
                        "parent_id": row.get("parent_station") or None,
                        "level_id": row.get("level_id") or None,
                        "wheelchair_boarding": wb_val,
                        "extras": _extras_json(row, _CONSUMED["stops_area"]),
                    })
            if stop_areas:
                self._flush_table(db, stats, "stop_area", stop_areas)
                logger.info("Imported %d stop areas", stats["stop_area"])
            if stops:
                self._flush_table(db, stats, "scheduled_stop_point", stops)
                logger.info("Imported %d stops", stats["scheduled_stop_point"])

            # 5. Pathways
            pathways = []
            for row in self._iter_csv_rows(zf, "pathways.txt"):
                bidir = row.get("is_bidirectional", "0")
                pathways.append({
                    "id": row["pathway_id"],
                    "from_stop_id": row["from_stop_id"],
                    "to_stop_id": row["to_stop_id"],
                    "pathway_mode": _safe_int(row.get("pathway_mode", "1"), default=1),
                    "is_bidirectional": bidir == "1",
                    "length": _safe_float(row.get("length", ""), "length", row["pathway_id"]),
                    "traversal_time": _safe_int(row.get("traversal_time", "")) or None,
                    "stair_count": _safe_int(row.get("stair_count", "")) or None,
                    "max_slope": _safe_float(row.get("max_slope", ""), "max_slope", row["pathway_id"]),
                    "min_width": _safe_float(row.get("min_width", ""), "min_width", row["pathway_id"]),
                    "signposted_as": row.get("signposted_as") or None,
                    "reversed_signposted_as": row.get("reversed_signposted_as") or None,
                    "extras": _extras_json(row, _CONSUMED["pathways"]),
                })
            if pathways:
                self._flush_table(db, stats, "pathway", pathways)
                logger.info("Imported %d pathways", stats["pathway"])

            # 6. DayTypes
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
                    "end_date": row["end_date"],
                    "extras": _extras_json(row, _CONSUMED["calendar"]),
                })
            if day_types:
                self._flush_table(db, stats, "day_type", day_types)
                logger.info("Imported %d day types", stats["day_type"])

            # 7. OperatingDayExceptions
            exceptions = []
            for row in self._iter_csv_rows(zf, "calendar_dates.txt"):
                exceptions.append({
                    "day_type_id": row["service_id"],
                    "date": row["date"],
                    "is_addition": row.get("exception_type") == "1",
                    "extras": _extras_json(row, _CONSUMED["calendar_dates"]),
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
                    self._flush_table(db, stats, "day_type", placeholder_day_types)
                self._flush_table(db, stats, "operating_day_exception", exceptions)
                logger.info("Imported %d calendar date exceptions", stats["operating_day_exception"])

            # 8 & 9. Trips and StopTimes
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
                    "departure_time": "00:00:00",
                    "wheelchair_accessible": _opt_int(row.get("wheelchair_accessible")),
                    "bikes_allowed": _opt_int(row.get("bikes_allowed")),
                    "shape_id": row.get("shape_id") or None,
                    "headsign": row.get("trip_headsign") or None,
                    "short_name": row.get("trip_short_name") or None,
                    "block_id": row.get("block_id") or None,
                    "extras": _extras_json(row, _CONSUMED["trips"]),
                })

            if journey_patterns:
                self._flush_table(db, stats, "journey_pattern", journey_patterns)

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
                    "arrival_time": row.get("arrival_time") or None,
                    "departure_time": row.get("departure_time") or None,
                    "pickup_type": _opt_int(row.get("pickup_type")),
                    "drop_off_type": _opt_int(row.get("drop_off_type")),
                    "timepoint": _opt_int(row.get("timepoint")),
                    "stop_headsign": row.get("stop_headsign") or None,
                    "shape_dist_traveled": _opt_float(row.get("shape_dist_traveled")),
                    "extras": _extras_json(row, _CONSUMED["stop_times"]),
                })

                if int(row["stop_sequence"]) in (0, 1) and row.get("departure_time"):
                    idx = sj_index.get(trip_id)
                    if idx is not None:
                        service_journeys[idx]["departure_time"] = row["departure_time"]

            if service_journeys:
                self._flush_table(db, stats, "service_journey", service_journeys)
                logger.info("Imported %d service journeys", stats["service_journey"])
            if points_in_jp:
                self._flush_table(db, stats, "point_in_journey_pattern", points_in_jp)
            if passing_times:
                self._flush_table(db, stats, "passing_time", passing_times)
                logger.info("Imported %d passing times", stats["passing_time"])

            # 10. FeedInfo
            feed_infos = []
            for row in self._iter_csv_rows(zf, "feed_info.txt"):
                feed_infos.append({
                    "id": "default_feed",
                    "publisher_name": row.get("feed_publisher_name") or "Unknown",
                    "publisher_url": row.get("feed_publisher_url") or "",
                    "lang": row.get("feed_lang") or "en",
                    "start_date": row.get("feed_start_date") or None,
                    "end_date": row.get("feed_end_date") or None,
                    "version": row.get("feed_version") or None,
                    "contact_email": row.get("feed_contact_email") or None,
                    "contact_url": row.get("feed_contact_url") or None,
                    "extras": _extras_json(row, _CONSUMED["feed_info"]),
                })
            if feed_infos:
                self._flush_table(db, stats, "feed_info", feed_infos)

            # 11. Shapes
            shape_points = []
            for row in self._iter_csv_rows(zf, "shapes.txt"):
                shape_points.append({
                    "shape_id": row["shape_id"],
                    "lat": float(row["shape_pt_lat"]),
                    "lon": float(row["shape_pt_lon"]),
                    "sequence": int(row["shape_pt_sequence"]),
                    "dist_traveled": _opt_float(row.get("shape_dist_traveled")),
                    "extras": _extras_json(row, _CONSUMED["shapes"]),
                })
            if shape_points:
                self._flush_table(db, stats, "shape_point", shape_points)
                logger.info("Imported %d shape points", stats["shape_point"])

            # 12. Frequencies
            frequencies = []
            for row in self._iter_csv_rows(zf, "frequencies.txt"):
                frequencies.append({
                    "service_journey_id": row["trip_id"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "headway_secs": int(row["headway_secs"]),
                    "exact_times": _opt_int(row.get("exact_times")) or 0,
                    "extras": _extras_json(row, _CONSUMED["frequencies"]),
                })
            if frequencies:
                self._flush_table(db, stats, "frequency", frequencies)
                logger.info("Imported %d frequencies", stats["frequency"])

            # 13. Transfers
            transfers = []
            for row in self._iter_csv_rows(zf, "transfers.txt"):
                transfers.append({
                    "from_stop_id": row["from_stop_id"],
                    "to_stop_id": row["to_stop_id"],
                    "transfer_type": _opt_int(row.get("transfer_type")) or 0,
                    "min_transfer_time": _opt_int(row.get("min_transfer_time")),
                    "extras": _extras_json(row, _CONSUMED["transfers"]),
                })
            if transfers:
                self._flush_table(db, stats, "transfer", transfers)
                logger.info("Imported %d transfers", stats["transfer"])

            # 14. Fare Attributes
            fare_attrs = []
            for row in self._iter_csv_rows(zf, "fare_attributes.txt"):
                fare_attrs.append({
                    "id": row["fare_id"],
                    "price": _opt_float(row.get("price")) or 0.0,
                    "currency_type": row.get("currency_type") or "",
                    "payment_method": _safe_int(row.get("payment_method", "0")),
                    "transfers": _opt_int(row.get("transfers")),
                    "operator_id": row.get("agency_id") or None,
                    "transfer_duration": _opt_int(row.get("transfer_duration")),
                    "extras": _extras_json(row, _CONSUMED["fare_attributes"]),
                })
            if fare_attrs:
                self._flush_table(db, stats, "fare_attribute", fare_attrs)
                logger.info("Imported %d fare attributes", stats["fare_attribute"])

            # 15. Fare Rules
            fare_rules = []
            for row in self._iter_csv_rows(zf, "fare_rules.txt"):
                fare_rules.append({
                    "fare_id": row["fare_id"],
                    "route_id": row.get("route_id", ""),
                    "origin_id": row.get("origin_id", ""),
                    "destination_id": row.get("destination_id", ""),
                    "contains_id": row.get("contains_id", ""),
                    "extras": _extras_json(row, _CONSUMED["fare_rules"]),
                })
            if fare_rules:
                self._flush_table(db, stats, "fare_rule", fare_rules)
                logger.info("Imported %d fare rules", stats["fare_rule"])

            # 16. Translations
            translations = []
            for row in self._iter_csv_rows(zf, "translations.txt"):
                translations.append({
                    "table_name": row.get("table_name", ""),
                    "field_name": row.get("field_name", ""),
                    "language": row.get("language", ""),
                    "translation": row.get("translation", ""),
                    "record_id": row.get("record_id", ""),
                    "record_sub_id": row.get("record_sub_id", ""),
                    "field_value": row.get("field_value", ""),
                    "extras": _extras_json(row, _CONSUMED["translations"]),
                })
            if translations:
                self._flush_table(db, stats, "translation", translations)
                logger.info("Imported %d translations", stats["translation"])

            # 17. Attributions
            attributions = []
            for i, row in enumerate(self._iter_csv_rows(zf, "attributions.txt")):
                ip = row.get("is_producer")
                io = row.get("is_operator")
                ia = row.get("is_authority")
                attributions.append({
                    "id": row.get("attribution_id") or f"attr_{i}",
                    "organization_name": row.get("organization_name") or "Unknown",
                    "is_producer": ip == "1" if ip and ip.strip() else None,
                    "is_operator": io == "1" if io and io.strip() else None,
                    "is_authority": ia == "1" if ia and ia.strip() else None,
                    "attribution_url": row.get("attribution_url") or None,
                    "attribution_email": row.get("attribution_email") or None,
                    "attribution_phone": row.get("attribution_phone") or None,
                    "extras": _extras_json(row, _CONSUMED["attributions"]),
                })
            if attributions:
                self._flush_table(db, stats, "attribution", attributions)
                logger.info("Imported %d attributions", stats["attribution"])

        if warnings:
            stats["_warnings"] = warnings
        logger.info("GTFS import complete: %d tables, %d warnings", len(stats) - (1 if warnings else 0), warnings)
        return stats
