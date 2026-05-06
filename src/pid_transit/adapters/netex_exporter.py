"""
NeTEx Exporter Adapter (Object-Oriented).
"""

import logging
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone
from typing import Union
from pathlib import Path

from ..core.database import TransmodelDatabase

logger = logging.getLogger(__name__)

DAY_FLAG_TO_NAME = [
    ("monday",    "Monday"),
    ("tuesday",   "Tuesday"),
    ("wednesday", "Wednesday"),
    ("thursday",  "Thursday"),
    ("friday",    "Friday"),
    ("saturday",  "Saturday"),
    ("sunday",    "Sunday"),
]


class NetexExporter:
    """
    Exports a TransmodelDatabase to a NeTEx XML file (PT-EPIP profile).

    Frames produced:
      ResourceFrame  — Operators
      SiteFrame      — StopPlaces (with coordinates)
      ServiceFrame   — Lines, ScheduledStopPoints, JourneyPatterns
      TimetableFrame — DayTypes, ServiceJourneys, TimetabledPassingTimes
    """

    def __init__(self, profile: str = "EPIP"):
        self.profile = profile
        self._nsmap = {
            "xmlns": "http://www.netex.org.uk/netex",
            "xmlns:siri": "http://www.siri.org.uk/siri",
            "xmlns:gml": "http://www.opengis.net/gml/3.2",
            "version": "1.1",
        }

    def export_from_db(self, db: TransmodelDatabase, output_path: Union[Path, str]) -> None:
        root = ET.Element("PublicationDelivery", attrib=self._nsmap)

        ET.SubElement(root, "PublicationTimestamp").text = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        ET.SubElement(root, "ParticipantRef").text = "PID_TRANSIT_SYSTEM"

        data_objects = ET.SubElement(root, "dataObjects")
        composite = ET.SubElement(data_objects, "CompositeFrame", attrib={
            "id": "PID:CompositeFrame:01",
            "version": "1",
        })
        frames = ET.SubElement(composite, "frames")

        self._write_resource_frame(db, frames)
        self._write_site_frame(db, frames)
        self._write_service_frame(db, frames)
        self._write_timetable_frame(db, frames)

        raw = ET.tostring(root, encoding="utf-8")
        pretty = minidom.parseString(raw).toprettyxml(indent="  ")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(pretty)

    # -------------------------------------------------------------------------
    # ResourceFrame — operators
    # -------------------------------------------------------------------------

    def _write_resource_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        frame = ET.SubElement(frames, "ResourceFrame", attrib={
            "id": "PID:ResourceFrame:01", "version": "1"
        })
        organisations = ET.SubElement(frame, "organisations")

        for op in db.get_records("operator"):
            el = ET.SubElement(organisations, "Operator", attrib={
                "id": op["id"], "version": "1"
            })
            ET.SubElement(el, "Name").text = op["name"]
            if op.get("phone"):
                contact = ET.SubElement(el, "ContactDetails")
                ET.SubElement(contact, "Phone").text = op["phone"]

    # -------------------------------------------------------------------------
    # SiteFrame — stop places (coordinates live here)
    # -------------------------------------------------------------------------

    def _write_site_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        stops = db.get_records("scheduled_stop_point")
        if not stops:
            return

        frame = ET.SubElement(frames, "SiteFrame", attrib={
            "id": "PID:SiteFrame:01", "version": "1"
        })
        stop_places = ET.SubElement(frame, "stopPlaces")

        for sp in stops:
            el = ET.SubElement(stop_places, "StopPlace", attrib={
                "id": sp["id"], "version": "1"
            })
            ET.SubElement(el, "Name").text = sp["name"]
            centroid = ET.SubElement(el, "Centroid")
            location = ET.SubElement(centroid, "Location")
            ET.SubElement(location, "Longitude").text = str(sp["lon"])
            ET.SubElement(location, "Latitude").text = str(sp["lat"])
            ET.SubElement(el, "StopPlaceType").text = "onstreetBus"

    # -------------------------------------------------------------------------
    # ServiceFrame — lines, scheduled stop points, journey patterns
    # -------------------------------------------------------------------------

    def _write_service_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        lines = db.get_records("line")
        stops = db.get_records("scheduled_stop_point")
        patterns = db.get_records("journey_pattern")
        points = db.get_records("point_in_journey_pattern")

        if not lines and not stops and not patterns:
            return

        frame = ET.SubElement(frames, "ServiceFrame", attrib={
            "id": "PID:ServiceFrame:01", "version": "1"
        })

        if lines:
            lines_el = ET.SubElement(frame, "lines")
            for line in lines:
                el = ET.SubElement(lines_el, "Line", attrib={
                    "id": line["id"], "version": "1"
                })
                ET.SubElement(el, "Name").text = line["name"]
                if line.get("short_name"):
                    ET.SubElement(el, "ShortName").text = line["short_name"]
                ET.SubElement(el, "TransportMode").text = line["transport_mode"]
                if line.get("operator_id"):
                    ET.SubElement(el, "OperatorRef", attrib={
                        "ref": line["operator_id"], "version": "1"
                    })

        if stops:
            ssps_el = ET.SubElement(frame, "scheduledStopPoints")
            for sp in stops:
                el = ET.SubElement(ssps_el, "ScheduledStopPoint", attrib={
                    "id": sp["id"], "version": "1"
                })
                ET.SubElement(el, "Name").text = sp["name"]

        if patterns:
            points_by_pattern: dict = {}
            for pt in points:
                points_by_pattern.setdefault(pt["journey_pattern_id"], []).append(pt)
            for pts in points_by_pattern.values():
                pts.sort(key=lambda p: p["order"])

            patterns_el = ET.SubElement(frame, "journeyPatterns")
            for jp in patterns:
                el = ET.SubElement(patterns_el, "JourneyPattern", attrib={
                    "id": jp["id"], "version": "1"
                })
                ET.SubElement(el, "LineRef", attrib={
                    "ref": jp["line_id"], "version": "1"
                })
                if jp.get("direction"):
                    ET.SubElement(el, "DirectionType").text = jp["direction"]

                pattern_pts = points_by_pattern.get(jp["id"], [])
                if pattern_pts:
                    seq_el = ET.SubElement(el, "pointsInSequence")
                    for pt in pattern_pts:
                        spi_id = f"{jp['id']}_stop_{pt['order']}"
                        spi = ET.SubElement(seq_el, "StopPointInJourneyPattern", attrib={
                            "id": spi_id,
                            "version": "1",
                            "order": str(pt["order"] + 1),  # NeTEx is 1-indexed
                        })
                        ET.SubElement(spi, "ScheduledStopPointRef", attrib={
                            "ref": pt["stop_point_id"], "version": "1"
                        })

    # -------------------------------------------------------------------------
    # TimetableFrame — day types, service journeys, passing times
    # -------------------------------------------------------------------------

    def _write_timetable_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        day_types = db.get_records("day_type")
        journeys = db.get_records("service_journey")
        passing_times = db.get_records("passing_time")

        if not day_types and not journeys:
            return

        frame = ET.SubElement(frames, "TimetableFrame", attrib={
            "id": "PID:TimetableFrame:01", "version": "1"
        })

        if day_types:
            dt_container = ET.SubElement(frame, "dayTypes")
            for dt in day_types:
                el = ET.SubElement(dt_container, "DayType", attrib={
                    "id": dt["id"], "version": "1"
                })
                active = " ".join(
                    name for flag, name in DAY_FLAG_TO_NAME if dt.get(flag)
                )
                if active:
                    pod = ET.SubElement(ET.SubElement(el, "properties"), "PropertyOfDay")
                    ET.SubElement(pod, "DaysOfWeek").text = active

        pt_by_journey: dict = {}
        for pt in passing_times:
            pt_by_journey.setdefault(pt["service_journey_id"], []).append(pt)
        for pts in pt_by_journey.values():
            pts.sort(key=lambda p: p["order"])

        if journeys:
            journeys_el = ET.SubElement(frame, "vehicleJourneys")
            for sj in journeys:
                el = ET.SubElement(journeys_el, "ServiceJourney", attrib={
                    "id": sj["id"], "version": "1"
                })
                ET.SubElement(el, "DayTypeRef", attrib={
                    "ref": sj["day_type_id"], "version": "1"
                })
                if sj.get("journey_pattern_id"):
                    ET.SubElement(el, "JourneyPatternRef", attrib={
                        "ref": sj["journey_pattern_id"], "version": "1"
                    })
                ET.SubElement(el, "LineRef", attrib={
                    "ref": sj["line_id"], "version": "1"
                })
                ET.SubElement(el, "DepartureTime").text = sj["departure_time"]

                sj_pts = pt_by_journey.get(sj["id"], [])
                if sj_pts:
                    pt_el = ET.SubElement(el, "passingTimes")
                    for pt in sj_pts:
                        tpt = ET.SubElement(pt_el, "TimetabledPassingTime", attrib={
                            "version": "0"
                        })
                        if sj.get("journey_pattern_id"):
                            ET.SubElement(tpt, "StopPointInJourneyPatternRef", attrib={
                                "ref": f"{sj['journey_pattern_id']}_stop_{pt['order']}",
                                "version": "1",
                            })
                        if pt.get("arrival_time"):
                            ET.SubElement(tpt, "ArrivalTime").text = pt["arrival_time"]
                        if pt.get("departure_time"):
                            ET.SubElement(tpt, "DepartureTime").text = pt["departure_time"]
