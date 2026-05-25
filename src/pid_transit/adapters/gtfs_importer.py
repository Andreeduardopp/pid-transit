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

    def _gtfs_route_type_to_mode(self, route_type) -> TransportMode:
        rt = _safe_int(str(route_type), default=3)
        _BASIC = {0: TransportMode.TRAM, 1: TransportMode.METRO,
                  2: TransportMode.RAIL, 3: TransportMode.BUS,
                  4: TransportMode.FERRY}
        if rt in _BASIC:
            return _BASIC[rt]
        # Extended route types (GTFS-extensions / Google Transit)
        if 100 <= rt <= 199: return TransportMode.RAIL
        if 200 <= rt <= 299: return TransportMode.BUS   # coach
        if 400 <= rt <= 499: return TransportMode.METRO
        if 700 <= rt <= 799: return TransportMode.BUS
        if 900 <= rt <= 999: return TransportMode.TRAM
        if 1000 <= rt <= 1099: return TransportMode.FERRY
        logger.info("Unmapped route_type %d, defaulting to OTHER", rt)
        return TransportMode.OTHER

    def import_to_db(self, db: TransmodelDatabase, archive_path: Union[Path, str]) -> Dict[str, int]:
        """Import GTFS zip into the database."""
        logger.info("Importing GTFS archive: %s", archive_path)
        stats = {}
        warnings = 0
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
                logger.info("Imported %d operators", stats["operator"])
            else:
                logger.warning("No agency.txt or no agencies found; operator references will be NULL")
                warnings += 1

            default_op_id = operators[0]["id"] if operators else None

            # 2. Lines
            lines = []
            for row in self._iter_csv_rows(zf, "routes.txt"):
                lines.append({
                    "id": row["route_id"],
                    "operator_id": row.get("agency_id") or default_op_id,
                    "name": row.get("route_long_name") or row.get("route_short_name") or "Unnamed",
                    "short_name": row.get("route_short_name"),
                    "transport_mode": self._gtfs_route_type_to_mode(row.get("route_type", "3")),
                    "color": row.get("route_color")
                })
            if lines:
                stats["line"] = db.upsert("line", lines)
                logger.info("Imported %d lines", stats["line"])

            # 3. Levels
            levels = []
            for row in self._iter_csv_rows(zf, "levels.txt"):
                idx = _safe_float(row.get("level_index", "0"), "level_index", row.get("level_id", ""))
                levels.append({
                    "id": row["level_id"],
                    "index": idx if idx is not None else 0.0,
                    "name": row.get("level_name"),
                })
            if levels:
                stats["level"] = db.upsert("level", levels)
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
                        "name": row.get("stop_name", "Unnamed Stop"),
                        "lat": lat,
                        "lon": lon,
                        "stop_area_id": row.get("parent_station"),
                        "wheelchair_boarding": wb_val,
                    })
                elif loc_type in ("1", "2", "3", "4"):
                    stop_areas.append({
                        "id": stop_id,
                        "name": row.get("stop_name", "Unnamed Stop Area"),
                        "lat": lat,
                        "lon": lon,
                        "location_type": int(loc_type),
                        "parent_id": row.get("parent_station") or None,
                        "level_id": row.get("level_id") or None,
                        "wheelchair_boarding": wb_val,
                    })
            if stop_areas:
                stats["stop_area"] = db.upsert("stop_area", stop_areas)
                logger.info("Imported %d stop areas", stats["stop_area"])
            if stops:
                stats["scheduled_stop_point"] = db.upsert("scheduled_stop_point", stops)
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
                })
            if pathways:
                stats["pathway"] = db.upsert("pathway", pathways)
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
                    "end_date": row["end_date"]
                })
            if day_types:
                stats["day_type"] = db.upsert("day_type", day_types)
                logger.info("Imported %d day types", stats["day_type"])

            # 7. OperatingDayExceptions
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
                logger.info("Imported %d service journeys", stats["service_journey"])
            if points_in_jp:
                stats["point_in_journey_pattern"] = db.upsert("point_in_journey_pattern", points_in_jp)
            if passing_times:
                stats["passing_time"] = db.upsert("passing_time", passing_times)
                logger.info("Imported %d passing times", stats["passing_time"])

            # 10. FeedInfo
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

            # 11. Shapes
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
                logger.info("Imported %d shape points", stats["shape_point"])

            # 12. Frequencies
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
                logger.info("Imported %d frequencies", stats["frequency"])

            # 13. Transfers
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
                logger.info("Imported %d transfers", stats["transfer"])

            # 14. Fare Attributes
            fare_attrs = []
            for row in self._iter_csv_rows(zf, "fare_attributes.txt"):
                tr = row.get("transfers")
                td = row.get("transfer_duration")
                fare_attrs.append({
                    "id": row["fare_id"],
                    "price": float(row.get("price", "0")),
                    "currency_type": row.get("currency_type", ""),
                    "payment_method": _safe_int(row.get("payment_method", "0")),
                    "transfers": int(tr) if tr and tr.strip() else None,
                    "operator_id": row.get("agency_id") or None,
                    "transfer_duration": int(td) if td and td.strip() else None,
                })
            if fare_attrs:
                stats["fare_attribute"] = db.upsert("fare_attribute", fare_attrs)
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
                })
            if fare_rules:
                stats["fare_rule"] = db.upsert("fare_rule", fare_rules)
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
                })
            if translations:
                stats["translation"] = db.upsert("translation", translations)
                logger.info("Imported %d translations", stats["translation"])

            # 17. Attributions
            attributions = []
            for i, row in enumerate(self._iter_csv_rows(zf, "attributions.txt")):
                ip = row.get("is_producer")
                io = row.get("is_operator")
                ia = row.get("is_authority")
                attributions.append({
                    "id": row.get("attribution_id") or f"attr_{i}",
                    "organization_name": row.get("organization_name", "Unknown"),
                    "is_producer": ip == "1" if ip and ip.strip() else None,
                    "is_operator": io == "1" if io and io.strip() else None,
                    "is_authority": ia == "1" if ia and ia.strip() else None,
                    "attribution_url": row.get("attribution_url") or None,
                    "attribution_email": row.get("attribution_email") or None,
                    "attribution_phone": row.get("attribution_phone") or None,
                })
            if attributions:
                stats["attribution"] = db.upsert("attribution", attributions)
                logger.info("Imported %d attributions", stats["attribution"])

        if warnings:
            stats["_warnings"] = warnings
        logger.info("GTFS import complete: %d tables, %d warnings", len(stats) - (1 if warnings else 0), warnings)
        return stats
