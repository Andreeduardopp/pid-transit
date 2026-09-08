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


# The CEN ServiceFrame is a sequence, not a bag: its children are valid only in
# schema order. Emitting them in the order that happens to be convenient is what
# put <serviceLinks> and <connections> in illegal positions, and a misplaced child
# does not fail alone — it breaks the frame's content model, so the ids inside it
# never register and every reference to them fails as a dangling keyref. The
# writer below appends in whatever order suits it and _order_frame_children puts
# them right at the end, so adding a container later cannot silently repeat this.
_SERVICE_FRAME_ORDER = (
    "Name", "lines", "scheduledStopPoints", "serviceLinks", "connections",
    "stopAssignments", "journeyPatterns",
)


def _order_frame_children(frame: ET.Element, order: tuple) -> None:
    """Sort a frame's children into schema order.

    Only for containers whose children are appended from several independent
    branches. Where the writer emits in one straight line — a journey's
    children, say — write them in order instead of sorting them afterwards.
    """
    rank = {tag: i for i, tag in enumerate(order)}
    try:
        frame[:] = sorted(frame, key=lambda el: rank[el.tag])
    except KeyError as e:
        raise ValueError(
            f"<{e.args[0]}> was added to <{frame.tag}> but is missing from its "
            f"schema order {order}; add it in the position the CEN sequence "
            f"gives it"
        ) from None


def _split_shape_by_stops(shape_points: list, pattern_pts: list, stop_coords: dict) -> list:
    """Cut a pattern's polyline into one segment per hop between its stops.

    NeTEx carries geometry on ServiceLinks — one link per pair of consecutive
    stops — so a GTFS shape, which is a single polyline for the whole pattern,
    has to be divided. Each stop is snapped to its nearest shape point, and the
    cut indices are forced strictly increasing so no hop is degenerate.

    Adjacent segments deliberately share their junction point, which makes the
    split loss-free: concatenating the segments and dropping each repeated
    junction rebuilds the original polyline exactly. ``NetexImporter`` relies on
    that when it reassembles a shape.

    Returns ``[(from_stop_id, to_stop_id, [shape_point, ...]), ...]``, or ``[]``
    when the pattern has fewer than two stops, or the shape has too few points
    to give every hop at least two.
    """
    if len(pattern_pts) < 2 or len(shape_points) < len(pattern_pts):
        return []

    def nearest(lat, lon, lo: int, hi: int) -> int:
        """Index of the closest shape point within [lo, hi]."""
        best, best_d = lo, None
        for i in range(lo, hi + 1):
            p = shape_points[i]
            d = (p["lat"] - lat) ** 2 + (p["lon"] - lon) ** 2
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best

    # First and last stops anchor the ends of the shape; the rest snap in order.
    # The search is bounded rather than clamped afterwards, so a stop can never
    # be snapped to an index the hops after it need. That is a correctness
    # tidy-up, not a speed one: `hi` sits only `remaining` points from the end,
    # so it trims about 2% of the scan (measured), and the split stays
    # O(stops x shape_points). Making it linear means giving up exact
    # nearest-point snapping for a monotone walk, which no feed on hand can
    # currently validate — see "What the cut costs" in NETEX_EXPORT.md.
    cuts = [0]
    for pt in pattern_pts[1:-1]:
        coord = stop_coords.get(pt["stop_point_id"])
        lo = cuts[-1] + 1
        # Leave room for the hops that still have to fit after this one.
        remaining = len(pattern_pts) - len(cuts) - 1
        hi = len(shape_points) - 1 - remaining
        cuts.append(nearest(coord[0], coord[1], lo, hi) if coord else lo)
    cuts.append(len(shape_points) - 1)

    return [
        (pattern_pts[i]["stop_point_id"],
         pattern_pts[i + 1]["stop_point_id"],
         shape_points[cuts[i]:cuts[i + 1] + 1])
        for i in range(len(cuts) - 1)
    ]


class NetexExporter:
    """
    Exports a TransmodelDatabase to a NeTEx XML file in an EPIP-style dialect.

    "EPIP" is the CEN European Passenger Information Profile: the frame layout
    below follows it, but the output is not yet checked against it, and
    ``profile`` is currently inert (roadmap task 9). Nothing here is specific to
    the Portuguese national profile.

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

            # A Quay per StopPlace, nested. European-profile converters resolve a
            # passing time to a physical stop via ScheduledStopPoint -> Quay, so
            # the Quays have to exist; the binding itself is a
            # PassengerStopAssignment in the ServiceFrame (see _write_service_frame).
            # These were emitted as a top-level <quays> sibling of <stopPlaces>
            # until 2026-09-07, which the CEN SiteFrame does not admit.
            quays_el = ET.SubElement(el, "quays")
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
                })
                ET.SubElement(el, "Name").text = sp["name"]

            # Bind each ScheduledStopPoint to its Quay. This was a QuayRef
            # *attribute* on ScheduledStopPoint until 2026-09-07 — convenient for
            # third-party loaders but not legal NeTEx: QuayRef is an element, and
            # ScheduledStopPoint admits it in neither form. PassengerStopAssignment
            # is the conformant binding, and the one our own importer already reads
            # (netex_importer.py, on_assignment).
            assignments_el = ET.SubElement(frame, "stopAssignments")
            for order, sp in enumerate(stops, start=1):
                psa = ET.SubElement(assignments_el, "PassengerStopAssignment", attrib={
                    "id": f"{sp['id']}:PSA", "version": "1", "order": str(order),
                })
                ET.SubElement(psa, "ScheduledStopPointRef", attrib={
                    "ref": sp["id"], "version": "1",
                })
                ET.SubElement(psa, "QuayRef", attrib={
                    "ref": f"{sp['id']}:Quay", "version": "1",
                })

        if patterns:
            points_by_pattern: dict = {}
            for pt in points:
                points_by_pattern.setdefault(pt["journey_pattern_id"], []).append(pt)
            for pts in points_by_pattern.values():
                pts.sort(key=lambda p: p["order"])

            if self._canonical_jps is not None:
                patterns = [jp for jp in patterns if jp["id"] in self._canonical_jps]

            # Shape geometry is carried by ServiceLinks, one per hop between
            # consecutive stops, referenced from the pattern's linksInSequence.
            # Until 2026-09-07 it was a projections/LinkSequenceProjection on the
            # JourneyPattern itself, which reads naturally but is not legal NeTEx:
            # a JourneyPattern admits no projections (verified against the CEN XSD
            # from two independent positions), and ServiceLink is the only host the
            # schema accepts for a LinkSequenceProjection.
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

            # Cut each pattern's polyline into per-hop segments up front: the
            # ServiceLinks must be written before journeyPatterns to satisfy the
            # ServiceFrame's element order.
            stop_coords = {
                s["id"]: (s["lat"], s["lon"])
                for s in stops if s.get("lat") is not None and s.get("lon") is not None
            }
            # Keyed by (shape, stop sequence), not by shape alone. A hop is a
            # link between two consecutive stops, so two patterns can only share
            # links if they run the same stops over the same shape — sharing by
            # shape alone handed a pattern the cuts made for a different stop
            # list, producing links that skipped stops it serves and named stops
            # it does not. Patterns that do match still share, so the geometry is
            # written once and the importer's shape de-duplication still holds.
            shape_links: dict = {}     # key -> [(link id, from, to, [points])]
            pattern_links: dict = {}   # jp id -> key into shape_links
            shape_variants: dict = {}  # shape id -> distinct cuts issued so far
            for jp in patterns:
                shape_id = jp_shape.get(jp["id"])
                if not shape_id:
                    continue
                pattern_pts = points_by_pattern.get(jp["id"], [])
                key = (shape_id, tuple(pt["stop_point_id"] for pt in pattern_pts))
                if key not in shape_links:
                    segments = _split_shape_by_stops(
                        shapes_by_id[shape_id], pattern_pts, stop_coords,
                    )
                    if segments:
                        # The first cut of a shape keeps the shape's own id, so
                        # the common one-cut-per-shape case round-trips to the
                        # same shape id it came from; further cuts are suffixed.
                        n = shape_variants[shape_id] = shape_variants.get(shape_id, 0) + 1
                        prefix = shape_id if n == 1 else f"{shape_id}:v{n}"
                        shape_links[key] = [
                            (f"{prefix}:link:{i}", frm, to, pts)
                            for i, (frm, to, pts) in enumerate(segments, start=1)
                        ]
                    else:
                        logger.warning(
                            "Shape %s has too few points (%d) for the %d stops of "
                            "JourneyPattern %s; exporting it without geometry",
                            shape_id, len(shapes_by_id[shape_id]),
                            len(pattern_pts), jp["id"],
                        )
                        # Memoized so the failed split is not retried for every
                        # other pattern that uses this shape and stop sequence.
                        shape_links[key] = []
                if shape_links[key]:
                    pattern_links[jp["id"]] = key

            if any(shape_links.values()):
                links_el = ET.SubElement(frame, "serviceLinks")
                # gml:id is xs:ID — an NCName — so it cannot simply mirror the
                # NeTEx object id, which is a far looser string: real feeds carry
                # '|' and other characters that are legal there and illegal here
                # (Porto's shape ids do). These ids are internal to the GML and
                # carry no meaning for a consumer, so number them instead.
                gml_seq = 0
                for segments in shape_links.values():
                    for link_id, from_stop, to_stop, seg_points in segments:
                        gml_seq += 1
                        link = ET.SubElement(links_el, "ServiceLink", attrib={
                            "id": link_id, "version": "1"
                        })
                        projections = ET.SubElement(link, "projections")
                        proj = ET.SubElement(projections, "LinkSequenceProjection", attrib={
                            "id": f"{link_id}:proj", "version": "1"
                        })
                        line_string = ET.SubElement(proj, "gml:LineString", attrib={
                            "gml:id": f"ls_{gml_seq}"
                        })
                        ET.SubElement(line_string, "gml:posList").text = " ".join(
                            f"{p['lat']} {p['lon']}" for p in seg_points
                        )
                        ET.SubElement(link, "FromPointRef", attrib={
                            "ref": from_stop, "version": "1"
                        })
                        ET.SubElement(link, "ToPointRef", attrib={
                            "ref": to_stop, "version": "1"
                        })

            patterns_el = ET.SubElement(frame, "journeyPatterns")
            for jp in patterns:
                el = ET.SubElement(patterns_el, "JourneyPattern", attrib={
                    "id": jp["id"], "version": "1"
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

                links_key = pattern_links.get(jp["id"])
                if links_key:
                    links_seq = ET.SubElement(el, "linksInSequence")
                    for order, (link_id, _f, _t, _p) in enumerate(
                            shape_links[links_key], start=1):
                        slijp = ET.SubElement(
                            links_seq, "ServiceLinkInJourneyPattern", attrib={
                                "id": f"{jp['id']}:inJP:{order}", "version": "1",
                                "order": str(order),
                            })
                        ET.SubElement(slijp, "ServiceLinkRef", attrib={
                            "ref": link_id, "version": "1"
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

        _order_frame_children(frame, _SERVICE_FRAME_ORDER)

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

                # After passingTimes: a (Template)ServiceJourney's children are a
                # schema sequence, and frequencyGroups follows passingTimes in it.
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
                        # Order is fixed by the schema here too: the two bounds,
                        # then the interval. (Our importer reads these by name,
                        # so the swap is invisible to a round-trip.)
                        ET.SubElement(hjg, "FirstDepartureTime").text = fr["start_time"]
                        ET.SubElement(hjg, "LastDepartureTime").text = fr["end_time"]
                        ET.SubElement(hjg, "ScheduledHeadwayInterval").text = (
                            f"PT{fr['headway_secs']}S"
                        )
