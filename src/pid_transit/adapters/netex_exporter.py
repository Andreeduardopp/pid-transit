"""
NeTEx Exporter Adapter (Object-Oriented).
"""

import logging
import xml.etree.ElementTree as ET
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


def _normalize_time(gtfs_time: str) -> tuple[str, int]:
    """Convert a GTFS time (may be >= 24:00:00) to (xs:time, day_offset)."""
    parts = gtfs_time.strip().split(":")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    day_offset = h // 24
    h = h % 24
    return f"{h:02d}:{m:02d}:{s:02d}", day_offset


def _iso_date(gtfs_date: str) -> str:
    """GTFS YYYYMMDD (or already dashed) -> ISO YYYY-MM-DD."""
    d = gtfs_date.replace("-", "").strip()
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


def _wheelchair_to_access(value) -> str:
    """GTFS wheelchair code -> NeTEx MobilityImpairedAccess LimitationStatus."""
    return {1: "true", 2: "false"}.get(value, "unknown")


# Internal TransportMode values whose NeTEx VehicleMode spelling differs.
# Values not listed here (bus, tram, rail, metro, ferry, funicular, monorail)
# are already valid NeTEx mode names and pass through unchanged.
_INTERNAL_MODE_TO_NETEX = {
    "trolleybus": "trolleyBus",
    "cable_car": "cableway",
    "gondola": "telecabin",
    "other": "unknown",
}


def _netex_mode(mode: str) -> str:
    return _INTERNAL_MODE_TO_NETEX.get(mode, mode)


def _write_accessibility(parent: ET.Element, entity_id: str, wheelchair) -> None:
    if wheelchair not in (1, 2):
        return
    assessment = ET.SubElement(parent, "AccessibilityAssessment", attrib={
        "id": f"{entity_id}:access", "version": "1"
    })
    ET.SubElement(assessment, "MobilityImpairedAccess").text = _wheelchair_to_access(wheelchair)


class NetexExporter:
    """
    Exports a TransmodelDatabase to a NeTEx XML file (PT-EPIP profile).

    Frames produced:
      ResourceFrame  — Operators
      SiteFrame      — StopPlaces (with coordinates)
      ServiceFrame   — Lines, ScheduledStopPoints, JourneyPatterns
      TimetableFrame — DayTypes, ServiceJourneys, TimetabledPassingTimes
    """

    def __init__(self, profile: str = "EPIP", deduplicate_patterns: bool = True, compress: bool = False):
        self.profile = profile
        self.deduplicate_patterns = deduplicate_patterns
        self.compress = compress
        self._nsmap = {
            "xmlns": "http://www.netex.org.uk/netex",
            "xmlns:siri": "http://www.siri.org.uk/siri",
            "xmlns:gml": "http://www.opengis.net/gml/3.2",
            "version": "1.1",
        }

    def _build_pattern_dedup_map(self, db: TransmodelDatabase) -> dict:
        """Build a mapping from original JP ids to canonical JP ids.

        Groups patterns by (line_id, direction, ordered stop sequence) — the §2
        quotient shared with ``TransitDataset.normalize_patterns()``.
        Returns {original_id: canonical_id} and the set of canonical IDs.
        """
        from ..core.patterns import build_pattern_dedup_map
        return build_pattern_dedup_map(db)

    def export_from_db(self, db: TransmodelDatabase, output_path: Union[Path, str]) -> None:
        logger.info("Exporting NeTEx to %s", output_path)
        if self.deduplicate_patterns:
            # Precondition (formalization §4 V17): dedup collapses patterns by
            # ordered stop sequence and assumes every journey follows its pattern.
            # Journeys that violate this can be silently corrupted, so warn.
            from ..core.patterns import find_pattern_violations
            violations = find_pattern_violations(db)
            if violations:
                logger.warning(
                    "deduplicate_patterns=True but %d service journey(s) do not "
                    "follow their JourneyPattern stop sequence (V17); their "
                    "geometry may be misrepresented after dedup. First offenders: %s",
                    len(violations), ", ".join(sorted(violations)[:5]),
                )
            self._jp_remap, self._canonical_jps = self._build_pattern_dedup_map(db)
        else:
            self._jp_remap = None
            self._canonical_jps = None

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
        self._write_service_calendar_frame(db, frames)
        self._write_timetable_frame(db, frames)

        # Pretty-print in place and stream straight to the file. Previously this
        # round-tripped the whole document through minidom
        # (ET.tostring -> minidom.parseString -> toprettyxml), which held the
        # ElementTree, a serialized byte string, a second far-heavier minidom DOM
        # and the final pretty string in memory simultaneously -- the dominant
        # cost in NeTEx export peak RSS. ET.indent + ElementTree.write keeps only
        # the ElementTree and serializes incrementally to the open file.
        ET.indent(root, space="  ")
        tree = ET.ElementTree(root)

        out_path = Path(output_path)
        if self.compress:
            import gzip
            with gzip.open(str(out_path) + ".gz", "wb") as f:
                tree.write(f, encoding="utf-8", xml_declaration=True)
        else:
            tree.write(str(out_path), encoding="utf-8", xml_declaration=True)

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
            if sp.get("lat") is not None and sp.get("lon") is not None:
                centroid = ET.SubElement(el, "Centroid")
                location = ET.SubElement(centroid, "Location")
                ET.SubElement(location, "Longitude").text = str(sp["lon"])
                ET.SubElement(location, "Latitude").text = str(sp["lat"])
            _write_accessibility(el, sp["id"], sp.get("wheelchair_boarding"))
            ET.SubElement(el, "StopPlaceType").text = "onstreetBus"

        # Emit Quays as direct children of the SiteFrame (siblings of stopPlaces),
        # not nested inside StopPlace. European-profile converters resolve a passing
        # time to a physical stop via ScheduledStopPoint.QuayRef -> Quay; a Quay only
        # registers when it appears as a top-level element (a Quay nested inside a
        # StopPlace is consumed with the StopPlace and never indexed). EPIP-faithful
        # tools (incl. our own importer) read StopPlace and ignore these. Additive.
        quays_el = ET.SubElement(frame, "quays")
        for sp in stops:
            quay = ET.SubElement(quays_el, "Quay", attrib={
                "id": f"{sp['id']}:Quay", "version": "1"
            })
            ET.SubElement(quay, "Name").text = sp["name"]
            if sp.get("lat") is not None and sp.get("lon") is not None:
                q_centroid = ET.SubElement(quay, "Centroid")
                q_location = ET.SubElement(q_centroid, "Location")
                ET.SubElement(q_location, "Longitude").text = str(sp["lon"])
                ET.SubElement(q_location, "Latitude").text = str(sp["lat"])
            _write_accessibility(quay, f"{sp['id']}:Quay", sp.get("wheelchair_boarding"))

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
                ET.SubElement(el, "TransportMode").text = _netex_mode(line["transport_mode"])
                if line.get("operator_id"):
                    ET.SubElement(el, "OperatorRef", attrib={
                        "ref": line["operator_id"], "version": "1"
                    })

        if stops:
            ssps_el = ET.SubElement(frame, "scheduledStopPoints")
            for sp in stops:
                el = ET.SubElement(ssps_el, "ScheduledStopPoint", attrib={
                    "id": sp["id"], "version": "1",
                    # Link to the Quay nested in the SiteFrame. European-profile
                    # converters use this attribute to resolve a passing time to a
                    # physical stop; our importer ignores it (round-trip-safe).
                    "QuayRef": f"{sp['id']}:Quay",
                })
                ET.SubElement(el, "Name").text = sp["name"]

        if patterns:
            points_by_pattern: dict = {}
            for pt in points:
                points_by_pattern.setdefault(pt["journey_pattern_id"], []).append(pt)
            for pts in points_by_pattern.values():
                pts.sort(key=lambda p: p["order"])

            if self._canonical_jps is not None:
                patterns = [jp for jp in patterns if jp["id"] in self._canonical_jps]

            # Shapes ride along on their journey pattern as a
            # LinkSequenceProjection (a JourneyPattern is a LinkSequence).
            # Patterns are mapped to the shape of their journeys; per-journey
            # shape variation within one pattern is not representable here.
            jp_shape: dict = {}
            shapes_by_id: dict = {}
            shape_points = db.get_records("shape_point")
            if shape_points:
                for sp in shape_points:
                    shapes_by_id.setdefault(sp["shape_id"], []).append(sp)
                for pts in shapes_by_id.values():
                    pts.sort(key=lambda p: p["sequence"])
                for sj in db.get_records("service_journey"):
                    sid = sj.get("shape_id")
                    jp_id = sj.get("journey_pattern_id")
                    if sid and jp_id and jp_id not in jp_shape and sid in shapes_by_id:
                        jp_shape[jp_id] = sid
                        if self._jp_remap:
                            jp_shape.setdefault(self._jp_remap.get(jp_id, jp_id), sid)

            patterns_el = ET.SubElement(frame, "journeyPatterns")
            for jp in patterns:
                el = ET.SubElement(patterns_el, "JourneyPattern", attrib={
                    "id": jp["id"], "version": "1"
                })
                if jp.get("direction"):
                    ET.SubElement(el, "DirectionType").text = jp["direction"]

                shape_id = jp_shape.get(jp["id"])
                if shape_id:
                    projections = ET.SubElement(el, "projections")
                    proj = ET.SubElement(projections, "LinkSequenceProjection", attrib={
                        "id": shape_id, "version": "1"
                    })
                    line_string = ET.SubElement(proj, "gml:LineString", attrib={
                        "gml:id": f"ls_{jp['id'].replace(':', '_')}"
                    })
                    ET.SubElement(line_string, "gml:posList").text = " ".join(
                        f"{p['lat']} {p['lon']}" for p in shapes_by_id[shape_id]
                    )

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

        transfers = db.get_records("transfer")
        if transfers:
            connections = ET.SubElement(frame, "connections")
            for i, tr in enumerate(transfers, 1):
                conn = ET.SubElement(connections, "Connection", attrib={
                    "id": f"PID:Connection:{i}", "version": "1"
                })
                if tr.get("transfer_type"):
                    key_list = ET.SubElement(conn, "keyList")
                    kv = ET.SubElement(key_list, "KeyValue")
                    ET.SubElement(kv, "Key").text = "gtfs_transfer_type"
                    ET.SubElement(kv, "Value").text = str(tr["transfer_type"])
                if tr.get("min_transfer_time") is not None:
                    wtd = ET.SubElement(conn, "WalkTransferDuration")
                    ET.SubElement(wtd, "DefaultDuration").text = f"PT{tr['min_transfer_time']}S"
                from_el = ET.SubElement(conn, "From")
                ET.SubElement(from_el, "ScheduledStopPointRef", attrib={
                    "ref": tr["from_stop_id"], "version": "1"
                })
                to_el = ET.SubElement(conn, "To")
                ET.SubElement(to_el, "ScheduledStopPointRef", attrib={
                    "ref": tr["to_stop_id"], "version": "1"
                })

    # -------------------------------------------------------------------------
    # ServiceCalendarFrame — day types
    # -------------------------------------------------------------------------

    def _write_service_calendar_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        day_types = db.get_records("day_type")
        if not day_types:
            return

        frame = ET.SubElement(frames, "ServiceCalendarFrame", attrib={
            "id": "PID:ServiceCalendarFrame:01", "version": "1"
        })
        dt_container = ET.SubElement(frame, "dayTypes")
        for dt in day_types:
            el = ET.SubElement(dt_container, "DayType", attrib={
                "id": dt["id"], "version": "1"
            })
            valid_between = ET.SubElement(el, "ValidBetween")
            ET.SubElement(valid_between, "FromDate").text = f"{_iso_date(dt['start_date'])}T00:00:00"
            ET.SubElement(valid_between, "ToDate").text = f"{_iso_date(dt['end_date'])}T23:59:59"
            active = " ".join(
                name for flag, name in DAY_FLAG_TO_NAME if dt.get(flag)
            )
            if active:
                pod = ET.SubElement(ET.SubElement(el, "properties"), "PropertyOfDay")
                ET.SubElement(pod, "DaysOfWeek").text = active

        exceptions = db.get_records("operating_day_exception")
        if exceptions:
            assignments = ET.SubElement(frame, "dayTypeAssignments")
            for i, exc in enumerate(exceptions, 1):
                a = ET.SubElement(assignments, "DayTypeAssignment", attrib={
                    "id": f"PID:DayTypeAssignment:{i}", "version": "1", "order": str(i)
                })
                ET.SubElement(a, "Date").text = _iso_date(exc["date"])
                ET.SubElement(a, "DayTypeRef", attrib={
                    "ref": exc["day_type_id"], "version": "1"
                })
                ET.SubElement(a, "isAvailable").text = (
                    "true" if exc["is_addition"] else "false"
                )

    # -------------------------------------------------------------------------
    # TimetableFrame — service journeys, passing times
    # -------------------------------------------------------------------------

    def _write_timetable_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        journeys = db.get_records("service_journey")
        passing_times = db.get_records("passing_time")

        if not journeys:
            return

        frame = ET.SubElement(frames, "TimetableFrame", attrib={
            "id": "PID:TimetableFrame:01", "version": "1"
        })

        pt_by_journey: dict = {}
        for pt in passing_times:
            pt_by_journey.setdefault(pt["service_journey_id"], []).append(pt)
        for pts in pt_by_journey.values():
            pts.sort(key=lambda p: p["order"])

        # Ordered StopPointInJourneyPattern ids per pattern, so a journey's
        # k-th passing time can reference the pattern's k-th point. Matching
        # positionally (not by the passing time's own `order` value) keeps the
        # refs valid when the two order columns use different bases.
        spi_ids_by_pattern: dict = {}
        for pt in db.get_records("point_in_journey_pattern"):
            spi_ids_by_pattern.setdefault(pt["journey_pattern_id"], []).append(
                (pt["order"], f"{pt['journey_pattern_id']}_stop_{pt['order']}"))
        for entries in spi_ids_by_pattern.values():
            entries.sort()

        freq_by_journey: dict = {}
        for fr in db.get_records("frequency"):
            freq_by_journey.setdefault(fr["service_journey_id"], []).append(fr)

        if journeys:
            journeys_el = ET.SubElement(frame, "vehicleJourneys")
            for sj in journeys:
                # Frequency-based journeys are TemplateServiceJourneys (the
                # ServiceJourney subtype that carries frequency groups).
                frequencies = freq_by_journey.get(sj["id"], [])
                tag = "TemplateServiceJourney" if frequencies else "ServiceJourney"
                el = ET.SubElement(journeys_el, tag, attrib={
                    "id": sj["id"], "version": "1"
                })
                _write_accessibility(el, sj["id"], sj.get("wheelchair_accessible"))
                dep_time, dep_offset = _normalize_time(sj["departure_time"])
                ET.SubElement(el, "DepartureTime").text = dep_time
                if dep_offset:
                    ET.SubElement(el, "DepartureDayOffset").text = str(dep_offset)
                day_types_el = ET.SubElement(el, "dayTypes")
                ET.SubElement(day_types_el, "DayTypeRef", attrib={
                    "ref": sj["day_type_id"], "version": "1"
                })
                jp_id = sj.get("journey_pattern_id")
                if jp_id and self._jp_remap:
                    jp_id = self._jp_remap.get(jp_id, jp_id)
                if jp_id:
                    ET.SubElement(el, "JourneyPatternRef", attrib={
                        "ref": jp_id, "version": "1"
                    })
                ET.SubElement(el, "LineRef", attrib={
                    "ref": sj["line_id"], "version": "1"
                })

                if frequencies:
                    groups_el = ET.SubElement(el, "frequencyGroups")
                    for i, fr in enumerate(frequencies, 1):
                        hjg = ET.SubElement(groups_el, "HeadwayJourneyGroup", attrib={
                            "id": f"{sj['id']}:hjg:{i}", "version": "1"
                        })
                        if fr.get("exact_times"):
                            key_list = ET.SubElement(hjg, "keyList")
                            kv = ET.SubElement(key_list, "KeyValue")
                            ET.SubElement(kv, "Key").text = "gtfs_exact_times"
                            ET.SubElement(kv, "Value").text = str(fr["exact_times"])
                        ET.SubElement(hjg, "ScheduledHeadwayInterval").text = (
                            f"PT{fr['headway_secs']}S"
                        )
                        ET.SubElement(hjg, "FirstDepartureTime").text = fr["start_time"]
                        ET.SubElement(hjg, "LastDepartureTime").text = fr["end_time"]

                sj_pts = pt_by_journey.get(sj["id"], [])
                if sj_pts:
                    spi_ids = [spi_id for _, spi_id
                               in spi_ids_by_pattern.get(jp_id, [])]
                    pt_el = ET.SubElement(el, "passingTimes")
                    for k, pt in enumerate(sj_pts):
                        tpt = ET.SubElement(pt_el, "TimetabledPassingTime", attrib={
                            "version": "0"
                        })
                        if jp_id and k < len(spi_ids):
                            ET.SubElement(tpt, "StopPointInJourneyPatternRef", attrib={
                                "ref": spi_ids[k],
                                "version": "1",
                            })
                        if pt.get("arrival_time"):
                            arr_t, arr_off = _normalize_time(pt["arrival_time"])
                            ET.SubElement(tpt, "ArrivalTime").text = arr_t
                            if arr_off:
                                ET.SubElement(tpt, "ArrivalDayOffset").text = str(arr_off)
                        if pt.get("departure_time"):
                            dep_t, dep_off = _normalize_time(pt["departure_time"])
                            ET.SubElement(tpt, "DepartureTime").text = dep_t
                            if dep_off:
                                ET.SubElement(tpt, "DepartureDayOffset").text = str(dep_off)
