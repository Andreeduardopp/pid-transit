"""
NeTEx Importer Adapter (Object-Oriented).
"""

import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Union
from pathlib import Path

from ..core.database import TransmodelDatabase

logger = logging.getLogger(__name__)

_DEFAULT_NS = {"n": "http://www.netex.org.uk/netex"}

_DAY_NAMES = {
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
}


def _text(el: Optional[ET.Element]) -> Optional[str]:
    if el is not None and el.text:
        return el.text.strip()
    return None


def _reconstruct_time(time_str: str, day_offset: int = 0) -> str:
    """Combine xs:time + DayOffset into a GTFS-style HH:MM:SS (may be >= 24)."""
    parts = time_str.strip().split(":")
    h = int(parts[0]) + day_offset * 24
    m = int(parts[1])
    s = int(float(parts[2])) if len(parts) > 2 else 0
    return f"{h:02d}:{m:02d}:{s:02d}"


class NetexImporter:
    """
    Adapter class for importing NeTEx XML files into the Transmodel database.
    """

    def __init__(self, fallback_timezone: str = "UTC"):
        self.fallback_timezone = fallback_timezone
        self._ns: Dict[str, str] = dict(_DEFAULT_NS)

    @staticmethod
    def _detect_namespace(root: ET.Element) -> Dict[str, str]:
        tag = root.tag
        if tag.startswith("{"):
            ns_uri = tag.split("}")[0].lstrip("{")
            return {"n": ns_uri}
        return dict(_DEFAULT_NS)

    def import_to_db(self, db: TransmodelDatabase, xml_path: Union[Path, str]) -> Dict[str, int]:
        """Import a NeTEx XML file into the database."""
        stats: Dict[str, int] = {}

        logger.info("Parsing NeTEx XML: %s", xml_path)
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError as exc:
            raise ImportError(f"Failed to parse NeTEx XML '{xml_path}': {exc}") from exc
        root = tree.getroot()

        self._ns = self._detect_namespace(root)

        self._import_operators(root, db, stats)
        self._import_stops(root, db, stats)
        self._import_day_types(root, db, stats)
        self._import_lines(root, db, stats)
        self._import_journey_patterns(root, db, stats)
        self._import_service_journeys(root, db, stats)

        return stats

    def _import_operators(self, root: ET.Element, db: TransmodelDatabase, stats: Dict[str, int]) -> None:
        operators = []
        for op in root.findall(".//n:Operator", self._ns):
            oid = op.attrib.get("id", "UNKNOWN")
            name = _text(op.find("n:Name", self._ns)) or "Unnamed"
            phone = None
            contact = op.find("n:ContactDetails", self._ns)
            if contact is not None:
                phone = _text(contact.find("n:Phone", self._ns))
            operators.append({
                "id": oid,
                "name": name,
                "timezone": self.fallback_timezone,
                "phone": phone,
            })
        if operators:
            stats["operator"] = db.upsert("operator", operators)
            logger.info("Imported %d operators", stats["operator"])

    def _import_stops(self, root: ET.Element, db: TransmodelDatabase, stats: Dict[str, int]) -> None:
        stops = []
        for sp in root.findall(".//n:StopPlace", self._ns):
            sid = sp.attrib.get("id")
            if not sid:
                continue
            name = _text(sp.find("n:Name", self._ns)) or "Unnamed"
            lat, lon = None, None
            centroid = sp.find("n:Centroid", self._ns)
            if centroid is not None:
                loc = centroid.find("n:Location", self._ns)
                if loc is not None:
                    lat_t = _text(loc.find("n:Latitude", self._ns))
                    lon_t = _text(loc.find("n:Longitude", self._ns))
                    if lat_t and lon_t:
                        try:
                            lat, lon = float(lat_t), float(lon_t)
                        except (ValueError, TypeError):
                            logger.warning("Invalid coordinates for StopPlace %s, skipping", sid)
                            continue
            if lat is not None and lon is not None:
                stops.append({
                    "id": sid,
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                })
        if stops:
            stats["scheduled_stop_point"] = db.upsert("scheduled_stop_point", stops)
            logger.info("Imported %d stops", stats["scheduled_stop_point"])

    def _import_day_types(self, root: ET.Element, db: TransmodelDatabase, stats: Dict[str, int]) -> None:
        day_types = []
        for dt in root.findall(".//n:DayType", self._ns):
            dtid = dt.attrib.get("id")
            if not dtid:
                continue
            flags = {k: False for k in _DAY_NAMES}
            props = dt.find("n:properties", self._ns)
            if props is not None:
                pod = props.find("n:PropertyOfDay", self._ns)
                if pod is not None:
                    dow_text = _text(pod.find("n:DaysOfWeek", self._ns)) or ""
                    active_days = {d.strip() for d in dow_text.split()}
                    for field_name, day_name in _DAY_NAMES.items():
                        if day_name in active_days:
                            flags[field_name] = True
            day_types.append({
                "id": dtid,
                **flags,
                "start_date": "19700101",
                "end_date": "20991231",
            })
        if day_types:
            stats["day_type"] = db.upsert("day_type", day_types)
            logger.info("Imported %d day types", stats["day_type"])

    def _import_lines(self, root: ET.Element, db: TransmodelDatabase, stats: Dict[str, int]) -> None:
        lines = []
        for line in root.findall(".//n:Line", self._ns):
            lid = line.attrib.get("id")
            if not lid:
                continue
            name = _text(line.find("n:Name", self._ns)) or "Unnamed"
            short_name = _text(line.find("n:ShortName", self._ns))
            mode = _text(line.find("n:TransportMode", self._ns)) or "bus"
            op_ref = line.find("n:OperatorRef", self._ns)
            operator_id = op_ref.attrib.get("ref") if op_ref is not None else None
            lines.append({
                "id": lid,
                "operator_id": operator_id,
                "name": name,
                "short_name": short_name,
                "transport_mode": mode,
            })
        if lines:
            stats["line"] = db.upsert("line", lines)
            logger.info("Imported %d lines", stats["line"])

    def _import_journey_patterns(self, root: ET.Element, db: TransmodelDatabase, stats: Dict[str, int]) -> None:
        patterns = []
        points = []

        # Resolve patterns that lack their own RouteRef via the service journeys
        # that reference them. Build the JourneyPatternRef -> LineRef map in a
        # single pass instead of rescanning every ServiceJourney per pattern
        # (which was an O(JP x SJ) full-tree scan).
        jp_to_line: Dict[str, str] = {}
        for sj in root.findall(".//n:ServiceJourney", self._ns):
            jp_ref = sj.find("n:JourneyPatternRef", self._ns)
            lr = sj.find("n:LineRef", self._ns)
            if jp_ref is not None and lr is not None:
                ref = jp_ref.attrib.get("ref")
                if ref and ref not in jp_to_line:
                    jp_to_line[ref] = lr.attrib.get("ref")

        for jp in root.findall(".//n:JourneyPattern", self._ns):
            jpid = jp.attrib.get("id")
            if not jpid:
                continue
            direction = _text(jp.find("n:DirectionType", self._ns))
            line_ref = jp.find("n:RouteRef", self._ns)
            line_id = None
            if line_ref is not None:
                line_id = line_ref.attrib.get("ref")

            if not line_id:
                line_id = jp_to_line.get(jpid)

            if not line_id:
                logger.warning("JourneyPattern %s has no line reference, skipping", jpid)
                continue

            patterns.append({
                "id": jpid,
                "line_id": line_id,
                "direction": direction,
            })

            seq = jp.find("n:pointsInSequence", self._ns)
            if seq is not None:
                for spi in seq.findall("n:StopPointInJourneyPattern", self._ns):
                    order = int(spi.attrib.get("order", 0))
                    ssp_ref = spi.find("n:ScheduledStopPointRef", self._ns)
                    if ssp_ref is not None:
                        points.append({
                            "journey_pattern_id": jpid,
                            "stop_point_id": ssp_ref.attrib.get("ref"),
                            "order": order - 1,
                        })

        if patterns:
            stats["journey_pattern"] = db.upsert("journey_pattern", patterns)
            logger.info("Imported %d journey patterns", stats["journey_pattern"])
        if points:
            stats["point_in_journey_pattern"] = db.upsert("point_in_journey_pattern", points)

    def _import_service_journeys(self, root: ET.Element, db: TransmodelDatabase, stats: Dict[str, int]) -> None:
        journeys = []
        passing_times = []

        # Pre-index each JourneyPattern's StopPointInJourneyPattern -> stop map
        # once. Previously this map was rebuilt by rescanning the whole XML tree
        # for every service journey, which is O(SJ x JP) and made round-trip
        # import of a large feed take many minutes.
        jp_spi_to_stop: Dict[str, Dict[str, str]] = {}
        for jp in root.findall(".//n:JourneyPattern", self._ns):
            jpid = jp.attrib.get("id")
            if not jpid:
                continue
            seq = jp.find("n:pointsInSequence", self._ns)
            if seq is None:
                continue
            mapping: Dict[str, str] = {}
            for spi in seq.findall("n:StopPointInJourneyPattern", self._ns):
                spi_id = spi.attrib.get("id")
                ssp_ref = spi.find("n:ScheduledStopPointRef", self._ns)
                if spi_id and ssp_ref is not None:
                    mapping[spi_id] = ssp_ref.attrib.get("ref")
            jp_spi_to_stop[jpid] = mapping

        for sj in root.findall(".//n:ServiceJourney", self._ns):
            sjid = sj.attrib.get("id")
            if not sjid:
                continue

            dep_time_text = _text(sj.find("n:DepartureTime", self._ns)) or "00:00:00"
            dep_offset_text = _text(sj.find("n:DepartureDayOffset", self._ns))
            dep_offset = int(dep_offset_text) if dep_offset_text else 0
            departure_time = _reconstruct_time(dep_time_text, dep_offset)

            line_ref = sj.find("n:LineRef", self._ns)
            line_id = line_ref.attrib.get("ref") if line_ref is not None else None

            jp_ref = sj.find("n:JourneyPatternRef", self._ns)
            jp_id = jp_ref.attrib.get("ref") if jp_ref is not None else None

            dt_ref = sj.find(".//n:DayTypeRef", self._ns)
            day_type_id = dt_ref.attrib.get("ref") if dt_ref is not None else "UNKNOWN"

            if not line_id:
                logger.warning("ServiceJourney %s has no LineRef, skipping", sjid)
                continue

            journeys.append({
                "id": sjid,
                "line_id": line_id,
                "journey_pattern_id": jp_id,
                "day_type_id": day_type_id,
                "departure_time": departure_time,
            })

            pt_container = sj.find("n:passingTimes", self._ns)
            if pt_container is not None:
                spi_to_stop = jp_spi_to_stop.get(jp_id, {}) if jp_id else {}

                order = 0
                for tpt in pt_container.findall("n:TimetabledPassingTime", self._ns):
                    stop_id = None
                    spi_ref = tpt.find("n:StopPointInJourneyPatternRef", self._ns)
                    if spi_ref is not None:
                        spi_id = spi_ref.attrib.get("ref")
                        stop_id = spi_to_stop.get(spi_id)

                    arr_time = None
                    arr_text = _text(tpt.find("n:ArrivalTime", self._ns))
                    if arr_text:
                        arr_off_text = _text(tpt.find("n:ArrivalDayOffset", self._ns))
                        arr_off = int(arr_off_text) if arr_off_text else 0
                        arr_time = _reconstruct_time(arr_text, arr_off)

                    dep_time = None
                    dep_text = _text(tpt.find("n:DepartureTime", self._ns))
                    if dep_text:
                        dep_off_text = _text(tpt.find("n:DepartureDayOffset", self._ns))
                        dep_off = int(dep_off_text) if dep_off_text else 0
                        dep_time = _reconstruct_time(dep_text, dep_off)

                    if stop_id:
                        passing_times.append({
                            "service_journey_id": sjid,
                            "stop_point_id": stop_id,
                            "order": order,
                            "arrival_time": arr_time,
                            "departure_time": dep_time,
                        })
                    order += 1

        if journeys:
            stats["service_journey"] = db.upsert("service_journey", journeys)
            logger.info("Imported %d service journeys", stats["service_journey"])
        if passing_times:
            stats["passing_time"] = db.upsert("passing_time", passing_times)
            logger.info("Imported %d passing times", stats["passing_time"])
