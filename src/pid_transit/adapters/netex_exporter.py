"""
NeTEx Exporter Adapter (Object-Oriented).
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import re
from typing import Dict, Optional, Tuple, Union
from pathlib import Path

from ..core.database import TransmodelDatabase
from ..core.schemas import TRANSMODEL_ENTITIES
from .netex_export_profiles import (
    DROPPED_PREFIX,
    ExportProfile,
    make_export_profile,
)

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


# CEN writes a colour as hexBinary: six hex digits, no '#'. GTFS agrees on the
# form but real feeds ship '#00AC00' and worse, and an invalid value here fails
# the whole document at the XSD rather than one Line. Anything that does not
# match is counted as a colour this export could not state.
_HEX_COLOUR = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def _colour(value) -> Optional[str]:
    """A GTFS colour as CEN hexBinary, or None if it is not one."""
    if not value:
        return None
    m = _HEX_COLOUR.match(str(value).strip())
    return m.group(1).upper() if m else None


def _wheelchair_to_access(value) -> str:
    """GTFS wheelchair code -> NeTEx MobilityImpairedAccess LimitationStatus."""
    return {1: "true", 2: "false"}.get(value, "unknown")


def _write_accessibility(parent: ET.Element, entity_id: str, wheelchair) -> None:
    # 0 is GTFS's "no information", which is a statement, not an absence:
    # until 2026-11 it was not written and came back empty.
    if wheelchair not in (0, 1, 2):
        return
    assessment = ET.SubElement(parent, "AccessibilityAssessment", attrib={
        "id": f"{entity_id}:access", "version": "1"
    })
    ET.SubElement(assessment, "MobilityImpairedAccess").text = _wheelchair_to_access(wheelchair)


def _write_centroid(parent: ET.Element, record: dict) -> None:
    if record.get("lat") is not None and record.get("lon") is not None:
        centroid = ET.SubElement(parent, "Centroid")
        location = ET.SubElement(centroid, "Location")
        ET.SubElement(location, "Longitude").text = str(record["lon"])
        ET.SubElement(location, "Latitude").text = str(record["lat"])


def _write_key_values(parent: ET.Element, values: Dict[str, str]) -> None:
    """A keyList of GTFS values NeTEx has no exact slot for. First child of any
    entity (CEN DataManagedObjectGroup comes before Name)."""
    if not values:
        return
    key_list = ET.SubElement(parent, "keyList")
    for key, value in values.items():
        kv = ET.SubElement(key_list, "KeyValue")
        ET.SubElement(kv, "Key").text = key
        ET.SubElement(kv, "Value").text = value


#: GTFS pathway_mode -> CEN AccessFeatureType. Fare and exit gates share
#: `barrier`, so the exact mode also travels as KeyValue `gtfs_pathway_mode`.
_PATHWAY_ACCESS_FEATURE = {
    1: "footpath", 2: "stairs", 3: "travelator", 4: "escalator",
    5: "lift", 6: "barrier", 7: "barrier",
}


class _StationLayout:
    """The station structure of a feed, grouped once for the SiteFrame writer.

    GTFS states a station tree by parent ids; NeTEx by containment. This reads
    the stop areas, levels and pathways a single time and answers which
    platforms, entrances, nodes, levels and pathways each station holds, so
    the writer never scans the feed per station. What it cannot place is
    counted here, against the profile, as the losses rule asks.
    """

    def __init__(self, db: TransmodelDatabase, stops: list, profile) -> None:
        areas = db.get_records("stop_area")
        self.levels = {lv["id"]: lv for lv in db.get_records("level")}
        self.stations = [a for a in areas if a["location_type"] == 1]
        station_ids = {a["id"] for a in self.stations}
        self.area_station: Dict[str, str] = {a["id"]: a["id"] for a in self.stations}

        self._children: Dict[tuple, list] = {}
        unplaced = 0
        for a in areas:
            if a["location_type"] == 1:
                continue
            if a["location_type"] in (2, 3) and a.get("parent_id") in station_ids:
                self._children.setdefault((a["parent_id"], a["location_type"]), []).append(a)
                self.area_station[a["id"]] = a["parent_id"]
            else:
                # Boarding areas (4), and entrances or nodes with no station.
                unplaced += 1
        profile.note_unsupported("stop_area", unplaced)

        self._platforms: Dict[str, list] = {}
        self.platform_station: Dict[str, str] = {}
        for sp in stops:
            if sp.get("stop_area_id") in station_ids:
                self._platforms.setdefault(sp["stop_area_id"], []).append(sp)
                self.platform_station[sp["id"]] = sp["stop_area_id"]

        # Which levels each station uses: its own, and its components'.
        used: Dict[str, list] = {}
        dangling = 0

        def use(station_id: str, level_id) -> None:
            nonlocal dangling
            if not level_id:
                return
            if level_id not in self.levels:
                dangling += 1
                return
            lst = used.setdefault(station_id, [])
            if level_id not in lst:
                lst.append(level_id)

        for st in self.stations:
            use(st["id"], st.get("level_id"))
        for (sid, _), children in self._children.items():
            for a in children:
                use(sid, a.get("level_id"))
        for sid, platforms in self._platforms.items():
            for sp in platforms:
                use(sid, sp.get("level_id"))
        profile.note_dropped("level_ref", dangling)
        self.station_levels = {sid: sorted(lst, key=lambda i: (self.levels[i]["index"], i))
                               for sid, lst in used.items()}
        written = {lid for lst in used.values() for lid in lst}
        profile.note_unsupported("level", len(self.levels.keys() - written))

        self.station_pathways: Dict[str, list] = {}
        self.frame_pathways: list = []
        unplaced = 0
        for pw in db.get_records("pathway"):
            ends = [self._station_of(pw["from_stop_id"]), self._station_of(pw["to_stop_id"])]
            if None in ends:
                unplaced += 1
            elif ends[0] == ends[1]:
                self.station_pathways.setdefault(ends[0], []).append(pw)
            else:
                self.frame_pathways.append(pw)
        profile.note_unsupported("pathway", unplaced)

    def _station_of(self, stop_id: str) -> Optional[str]:
        return self.platform_station.get(stop_id) or self.area_station.get(stop_id)

    def children(self, station_id: str, location_type: int) -> list:
        return self._children.get((station_id, location_type), [])

    def platforms(self, station_id: str) -> list:
        return self._platforms.get(station_id, [])

    def level_ref(self, station_id: str, level_id) -> Optional[str]:
        """The id of a station's copy of a GTFS level, or None."""
        if not level_id or level_id not in self.levels:
            return None
        return f"{station_id}:{level_id}"

    def place_ref(self, stop_id: str) -> str:
        """What a PathLink end points at: a platform's Quay, else the element
        that carries the stop area's own id."""
        if stop_id in self.platform_station:
            return f"{stop_id}:Quay"
        return stop_id


# The CEN ServiceFrame is a sequence, not a bag: its children are valid only in
# schema order. Emitting them in the order that happens to be convenient is what
# put <serviceLinks> and <connections> in illegal positions, and a misplaced child
# does not fail alone — it breaks the frame's content model, so the ids inside it
# never register and every reference to them fails as a dangling keyref. The
# writer below appends in whatever order suits it and _order_frame_children puts
# them right at the end, so adding a container later cannot silently repeat this.
#
# The order below is the CEN ServiceFrameGroup sequence, verified element by
# element against the vendored XSD: NetworkInFrameGroup (Network) -> Route ->
# Line (lines, groupsOfLines, destinationDisplays) -> Service
# (scheduledStopPoints, serviceLinks, ..., connections) -> stopAssignments ->
# journeyPatterns -> notices. ValidBetween and TypeOfFrameRef come from the
# frame's own EntityInVersion/VersionFrame groups and precede all of it.
_SERVICE_FRAME_ORDER = (
    "ValidBetween", "TypeOfFrameRef", "Name",
    "Network", "routes", "lines", "groupsOfLines", "destinationDisplays",
    "scheduledStopPoints", "serviceLinks", "connections",
    "stopAssignments", "journeyPatterns", "notices", "noticeAssignments",
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
    Exports a TransmodelDatabase to a NeTEx XML file, in a chosen profile.

    ``profile`` selects an `ExportProfile` (``"epip"`` by default, ``"pt"`` for
    the Portuguese national profile); every decision that differs between them
    is asked of that object rather than branched on here. See
    `netex_export_profiles` for what a profile may decide, and
    ``research/docs/perfil-pt/matriz-conformidade.md`` for the requirement each
    PT override answers.

    Frames produced (EPIP):
      ResourceFrame  — Operators
      SiteFrame      — StopPlaces (with coordinates); a GTFS station is one
                       StopPlace holding its levels, entrances, platforms
                       (Quays), generic nodes (AccessSpaces) and pathways
                       (SitePathLinks)
      ServiceFrame   — Lines, ScheduledStopPoints, JourneyPatterns
      TimetableFrame — DayTypes, ServiceJourneys, TimetabledPassingTimes

    ``export_from_db`` returns what this document does not say, by the same rule
    the importers follow for what they could not read: ``unsupported_<entity>``
    for an entity the profile has no element for, ``unsupported_field_<entity>_
    <field>`` for a field it has no slot for, and ``dropped_<what>`` for a datum
    the writer had a slot for and could not fit. Zero adds no key, so the keys
    that appear are exactly the losses that happened.

    An EPIP export does *not* return ``{}`` for a rich feed, and that is the
    point: GTFS carries block ids, headsigns and fares this model keeps and
    this writer has no NeTEx home for. Until 2026-09-10 it returned
    ``{}`` anyway, which was not fidelity but silence about six fields --
    including the timezone (see NETEX_EXPORT_AUDITORIA.md).
    """

    def __init__(self, profile: "ExportProfile | str | None" = "EPIP",
                 deduplicate_patterns: bool = True, compress: bool = False):
        self.profile = make_export_profile(profile)
        self.deduplicate_patterns = deduplicate_patterns
        self.compress = compress
        self._nsmap = {
            "xmlns": "http://www.netex.org.uk/netex",
            "xmlns:siri": "http://www.siri.org.uk/siri",
            "xmlns:gml": "http://www.opengis.net/gml/3.2",
            "version": "1.1",
        }

    @staticmethod
    def _feed_validity(db: TransmodelDatabase) -> "tuple[str, str] | None":
        """The window every frame is valid over: the union of the day types'.

        EPIP wants a ValidBetween on each frame and the model has no feed-level
        one, so it is derived from the calendar rather than invented. A feed
        with no day types has no window, and the frames get none.
        """
        rows = db.get_records("day_type")
        if not rows:
            return None
        # Normalise before comparing, not after: GTFS dates are YYYYMMDD and
        # sort as strings, but a feed that carries a dashed date mixes two
        # widths in one comparison and '-' sorts below every digit, so the
        # dashed one wins every min() and loses every max() regardless of when
        # it falls.
        starts = [_iso_date(r["start_date"]) for r in rows]
        ends = [_iso_date(r["end_date"]) for r in rows]
        return (min(starts), max(ends))

    def _stop_type(self, stop_id: str) -> str:
        """The StopPlaceType for a stop, decided once.

        Both the SiteFrame's StopPlace and (under a profile that locates its
        stop points) the ServiceFrame's ScheduledStopPoint state it, and
        `stop_place_type` counts a value the profile's enumeration does not
        admit. Asking twice would report one untypable stop as two.
        """
        if stop_id not in self._stop_types:
            self._stop_types[stop_id] = self.profile.stop_place_type(
                self._stop_modes.get(stop_id, self._default_stop_mode))
        return self._stop_types[stop_id]

    def _document_locale(self, operators: list,
                         feed_info: list) -> Tuple[Optional[str], Optional[str]]:
        """The timezone and language this document states, and what stating one costs.

        NeTEx says these once, on the frame; the model keeps them per operator,
        because GTFS keeps them per agency. GTFS also requires every agency in a
        feed to share a timezone, so the usual answer is that nothing is lost.
        A feed whose agencies disagree loses the ones the document cannot speak
        for, and they are counted rather than quietly outvoted.

        `Operator.timezone` is a required field of this model, and every time in
        a NeTEx file is local to it: dropping it does not lose a label, it moves
        every passing time in the feed. That is the whole of achado 1.
        """
        profile = self.profile
        zones = [op["timezone"] for op in operators if op.get("timezone")]
        op_langs = [op["lang"] for op in operators if op.get("lang")]
        feed_langs = [f["lang"] for f in feed_info if f.get("lang")]

        if not profile.write_frame_defaults:
            # No FrameDefaults, no locale: the profile has nowhere to say either.
            profile.note_unwritten_field("operator", "timezone", len(zones))
            profile.note_unwritten_field("operator", "lang", len(op_langs))
            return None, None

        tz_name = zones[0] if zones else None
        language = (op_langs or feed_langs or [None])[0]
        profile.note_dropped("operator_timezone",
                             sum(1 for z in zones if z != tz_name))
        profile.note_dropped("operator_lang",
                             sum(1 for lg in op_langs if lg != language))
        profile.note_dropped("feed_info_lang",
                             sum(1 for lg in feed_langs if lg != language))
        return tz_name, language

    @staticmethod
    def _publication_description(feed_info: list) -> Optional[str]:
        """One line of provenance for the document header, or None."""
        if not feed_info:
            return None
        info = feed_info[0]
        name = (info.get("publisher_name") or "").strip()
        version = (info.get("version") or "").strip()
        if not name:
            return None
        return f"{name} — feed version {version}" if version else name

    def _account_for_unwritten(self, db: TransmodelDatabase) -> None:
        """Count every field and every entity the model holds and the file omits.

        Derived from `schemas.py` against the profile's `WRITTEN_FIELDS`, not
        from a list of known omissions: a column added to the model and to no
        writer is counted here by default, which is the only version of this
        that stays true after the next change. One aggregate query per table,
        so the accounting does not scale with the feed the way a row walk would.

        An entity absent from `WRITTEN_FIELDS` is one no writer emits at all and
        is counted whole. An entity the *profile* cannot carry keeps its fields
        listed and is counted whole by the writer that skipped it, so the loss
        is stated once rather than twice in two shapes.
        """
        profile = self.profile
        for table, model in TRANSMODEL_ENTITIES.items():
            unwritten = profile.unwritten_fields(table, list(model.model_fields))
            if unwritten is None:
                profile.note_unsupported(table, db.count(table))
                continue
            if not unwritten:
                continue
            for field, n in db.count_non_null(table, unwritten).items():
                profile.note_unwritten_field(table, field, n)

    def _build_pattern_dedup_map(self, db: TransmodelDatabase) -> dict:
        """Build a mapping from original JP ids to canonical JP ids.

        Groups patterns by (line_id, direction, ordered stop sequence) — the §2
        quotient shared with ``TransitDataset.normalize_patterns()`` — and by
        shape, because a NeTEx pattern carries one shape: two patterns over the
        same stops but different shapes stay two patterns.
        Returns {original_id: canonical_id} and the set of canonical IDs.
        """
        from ..core.patterns import build_pattern_dedup_map
        return build_pattern_dedup_map(db, by_shape=True)

    def export_from_db(self, db: TransmodelDatabase,
                       output_path: Union[Path, str]) -> Dict[str, int]:
        # Resolved before the log line, so the path reported is the path
        # written: `compress` used to append ".gz" at the end of the run, after
        # announcing the name without it.
        out_path = Path(output_path)
        if self.compress and out_path.suffix != ".gz":
            out_path = out_path.with_name(out_path.name + ".gz")
        logger.info("Exporting NeTEx to %s (profile %s)",
                    out_path, self.profile.name)
        # The losses this call returns describe this document, not every
        # document this exporter has written.
        self.profile.begin_run()
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

        profile = self.profile
        self._validity = self._feed_validity(db)
        # Which mode serves which stop, so a StopPlace can be typed by the
        # lines that call there instead of assumed to be a bus stop. Read once
        # here because both the SiteFrame and the ServiceFrame need it.
        self._stop_modes = db.transport_modes_by_stop_point()
        # A stop no pattern calls at has no mode of its own. In a feed that
        # runs one mode there is still an honest answer -- that mode -- and
        # using it keeps an unserved stop from being typed "other" in a network
        # where nothing else could serve it.
        distinct = set(self._stop_modes.values())
        self._default_stop_mode = distinct.pop() if len(distinct) == 1 else None
        self._stop_types: Dict[str, str] = {}
        # Patterns the document cannot state a stop sequence for; filled by the
        # ServiceFrame writer and read by the TimetableFrame writer, which must
        # not then reference points that were never written.
        self._degenerate_jps: set = set()

        # Read once, up front: the locale has to be written on the CompositeFrame,
        # which is built before the frame that holds the operators it comes from.
        operators = db.get_records("operator")
        feed_info = db.get_records("feed_info")
        tz_name, language = self._document_locale(operators, feed_info)

        root = ET.Element("PublicationDelivery", attrib=self._nsmap)

        ET.SubElement(root, "PublicationTimestamp").text = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        ET.SubElement(root, "ParticipantRef").text = "PID_TRANSIT_SYSTEM"
        # Last of the CEN PublicationHeaderGroup, before dataObjects. Prose, not
        # data: the machine-readable provenance is the DataSource in the
        # ResourceFrame, and this is the line a person opening the file reads.
        description = self._publication_description(feed_info)
        if description:
            ET.SubElement(root, "Description").text = description

        data_objects = ET.SubElement(root, "dataObjects")
        composite = ET.SubElement(
            data_objects, "CompositeFrame",
            attrib=profile.frame_attrs("CompositeFrame"))
        # ValidBetween and TypeOfFrameRef, then codespaces then FrameDefaults
        # then frames: the CEN VersionFrameGroup sequence, and the order the PT
        # examples show. The composite carries the same header as the frames it
        # holds -- it is a frame, and it was the one that never got one.
        profile.write_frame_header(composite, "CompositeFrame", self._validity)
        profile.write_codespaces(composite)
        profile.write_composite_frame_defaults(composite, tz_name, language)
        frames = profile.container(composite, "frames")

        self._write_resource_frame(operators, feed_info, frames)
        self._write_site_frame(db, frames)
        self._write_service_frame(db, frames)
        self._write_service_calendar_frame(db, frames)
        self._write_timetable_frame(db, frames)

        # Everything the model holds and none of the writers above placed. Done
        # here rather than inside each writer so that an entity the export
        # skipped entirely (no journeys, no shapes) is still accounted for.
        self._account_for_unwritten(db)

        # Pretty-print in place and stream straight to the file. Previously this
        # round-tripped the whole document through minidom
        # (ET.tostring -> minidom.parseString -> toprettyxml), which held the
        # ElementTree, a serialized byte string, a second far-heavier minidom DOM
        # and the final pretty string in memory simultaneously -- the dominant
        # cost in NeTEx export peak RSS. ET.indent + ElementTree.write keeps only
        # the ElementTree and serializes incrementally to the open file.
        ET.indent(root, space="  ")
        tree = ET.ElementTree(root)

        if self.compress:
            import gzip
            with gzip.open(out_path, "wb") as f:
                tree.write(f, encoding="utf-8", xml_declaration=True)
        else:
            tree.write(str(out_path), encoding="utf-8", xml_declaration=True)

        # What the profile could not carry travels in the return value, not
        # only in a log line -- the same rule the importer follows for what it
        # could not read. Zero adds no key, so an EPIP export of an ordinary
        # feed still returns {}.
        for key, count in sorted(profile.losses.items()):
            if key.startswith(DROPPED_PREFIX):
                logger.warning("Profile %s dropped %d %s", profile.name, count, key)
            else:
                logger.warning("Profile %s could not carry %d %s",
                               profile.name, count, key)
        return dict(profile.losses)

    # -------------------------------------------------------------------------
    # ResourceFrame — operators
    # -------------------------------------------------------------------------

    def _write_resource_frame(self, operators: list, feed_info: list,
                              frames: ET.Element) -> None:
        profile = self.profile
        frame = ET.SubElement(frames, "ResourceFrame",
                              attrib=profile.frame_attrs("ResourceFrame"))
        profile.write_frame_header(frame, "ResourceFrame", self._validity)
        # dataSources before organisations: the CEN ResourceFrameGroup opens
        # with DataManagementInFrameGroup and reaches the organisations three
        # groups later.
        if feed_info and profile.carries("feed_info"):
            self._write_data_sources(frame, feed_info)
        # typesOfValue sits between them: the CEN ResourceFrameGroup runs
        # DataManagementInFrameGroup -> TypeOfValueInFrameGroup ->
        # OrganisationInFrameGroup.
        profile.write_types_of_frame(frame)
        organisations = profile.container(frame, "organisations")

        for op in operators:
            # Operator or Authority: the PT profile lists only the latter as a
            # concrete Organisation, and its Line references it accordingly.
            el = ET.SubElement(organisations, profile.organisation_element,
                               attrib=profile.entity_attrs(op["id"]))
            ET.SubElement(el, "Name").text = op["name"]
            url = op.get("url") if profile.states("operator", "url") else None
            phone = op.get("phone") if profile.states("operator", "phone") else None
            if phone or url:
                # CEN ContactDetailsStructure is a sequence: ContactPerson,
                # Email, Phone, Fax, Url. Phone before Url, not the order the
                # PT example happens to show (it has only Url).
                contact = ET.SubElement(el, "ContactDetails")
                if phone:
                    ET.SubElement(contact, "Phone").text = phone
                if url:
                    ET.SubElement(contact, "Url").text = url

    def _write_data_sources(self, frame: ET.Element, feed_info: list) -> None:
        """`feed_info` as a DataSource — who published this file, and for when.

        The GTFS feed_info table has no NeTEx element named after it, and the
        whole record was leaving the document: publisher, validity, version,
        contact, and with them the provenance of the file (achado 1). DataSource
        is where CEN keeps the provenance of a dataset, and it has a slot for
        almost all of it — it extends TypeOfValue, so it inherits Name and Url,
        and adds Email of its own, on top of the ValidBetween and keyList every
        versioned object gets.

        The two with no slot ride in the keyList, the way gtfs_transfer_type and
        gtfs_exact_times already do. CEN order, which is what the writer follows
        here: ValidBetween, keyList, Name, Url, Email.
        """
        profile = self.profile
        container = profile.container(frame, "dataSources")
        for info in feed_info:
            el = ET.SubElement(container, "DataSource",
                               attrib=profile.entity_attrs(info["id"]))
            start = info.get("start_date") if profile.states("feed_info", "start_date") else None
            end = info.get("end_date") if profile.states("feed_info", "end_date") else None
            if start and end:
                valid = ET.SubElement(el, "ValidBetween")
                ET.SubElement(valid, "FromDate").text = f"{_iso_date(start)}T00:00:00"
                ET.SubElement(valid, "ToDate").text = f"{_iso_date(end)}T23:59:59"
            keyed = [(k, v) for k, f, v in (
                ("gtfs_feed_version", "version", info.get("version")),
                ("gtfs_feed_contact_url", "contact_url", info.get("contact_url")),
            ) if v and profile.states("feed_info", f)]
            if keyed:
                key_list = ET.SubElement(el, "keyList")
                for key, value in keyed:
                    kv = ET.SubElement(key_list, "KeyValue")
                    ET.SubElement(kv, "Key").text = key
                    ET.SubElement(kv, "Value").text = str(value)
            ET.SubElement(el, "Name").text = info["publisher_name"]
            if info.get("publisher_url") and profile.states("feed_info", "publisher_url"):
                ET.SubElement(el, "Url").text = info["publisher_url"]
            if info.get("contact_email") and profile.states("feed_info", "contact_email"):
                ET.SubElement(el, "Email").text = info["contact_email"]

    # -------------------------------------------------------------------------
    # SiteFrame — stop places (coordinates live here)
    # -------------------------------------------------------------------------

    def _write_site_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        stops = db.get_records("scheduled_stop_point")
        if not stops:
            return

        profile = self.profile
        frame = ET.SubElement(frames, "SiteFrame",
                              attrib=profile.frame_attrs("SiteFrame"))
        profile.write_frame_header(frame, "SiteFrame", self._validity)
        stop_places = profile.container(frame, "stopPlaces")

        states_wheelchair = profile.states("scheduled_stop_point",
                                          "wheelchair_boarding")
        layout = (_StationLayout(db, stops, profile)
                  if profile.writes_station_structure and profile.writes_quays
                  else None)

        # Stations first: each one a StopPlace holding its platforms as Quays.
        if layout is not None:
            for station in layout.stations:
                self._write_station(stop_places, station, layout, states_wheelchair)

        for sp in stops:
            if layout is not None and sp["id"] in layout.platform_station:
                continue
            if layout is not None:
                # A platform with no station to sit in has no Level to point at.
                if sp.get("level_id"):
                    profile.note_dropped("scheduled_stop_point_level_id")
                if sp.get("stop_area_id"):
                    profile.note_dropped("scheduled_stop_point_stop_area_id")
            wheelchair = sp.get("wheelchair_boarding") if states_wheelchair else None
            el = ET.SubElement(stop_places, "StopPlace",
                               attrib=profile.entity_attrs(sp["id"]))
            ET.SubElement(el, "Name").text = sp["name"]
            if profile.states("scheduled_stop_point", "code") and sp.get("code"):
                ET.SubElement(el, "PrivateCode").text = sp["code"]
            _write_centroid(el, sp)
            _write_accessibility(el, sp["id"], wheelchair)
            ET.SubElement(el, "StopPlaceType").text = self._stop_type(sp["id"])

            if not profile.writes_quays:
                # The PT ServiceFrame has no stopAssignments, so a Quay would
                # have nothing to bind it to a ScheduledStopPoint; the profile
                # gives the two the same id instead. Nothing is lost, so
                # nothing is counted.
                continue

            # A Quay per StopPlace, nested. European-profile converters resolve a
            # passing time to a physical stop via ScheduledStopPoint -> Quay, so
            # the Quays have to exist; the binding itself is a
            # PassengerStopAssignment in the ServiceFrame (see _write_service_frame).
            # These were emitted as a top-level <quays> sibling of <stopPlaces>
            # until 2026-09-07, which the CEN SiteFrame does not admit.
            quays_el = ET.SubElement(el, "quays")
            self._write_quay(quays_el, sp, wheelchair, level_ref=None)

        if layout is not None and layout.frame_pathways:
            # A pathway whose two ends sit in different stations belongs to
            # neither StopPlace; the SiteFrame holds it (CEN PathInFrameGroup).
            links = ET.SubElement(frame, "pathLinks")
            for pw in layout.frame_pathways:
                self._write_path_link(links, "PathLink", pw, layout)

    def _write_quay(self, parent: ET.Element, sp: dict, wheelchair,
                    level_ref: Optional[str], in_station: bool = False) -> None:
        """A platform's Quay, children in CEN Quay order.

        A platform alone in its own StopPlace keeps its rider-facing code on
        that StopPlace, as it always has; one inside a station has only the
        Quay to carry it.
        """
        profile = self.profile
        quay = ET.SubElement(parent, "Quay",
                             attrib=profile.entity_attrs(f"{sp['id']}:Quay"))
        ET.SubElement(quay, "Name").text = sp["name"]
        if (in_station and sp.get("code")
                and profile.states("scheduled_stop_point", "code")):
            ET.SubElement(quay, "PrivateCode").text = sp["code"]
        _write_centroid(quay, sp)
        _write_accessibility(quay, f"{sp['id']}:Quay", wheelchair)
        if level_ref:
            ET.SubElement(quay, "LevelRef", attrib=profile.ref_attrs(level_ref))
        if (sp.get("platform_code")
                and profile.states("scheduled_stop_point", "platform_code")):
            ET.SubElement(quay, "PublicCode").text = sp["platform_code"]

    def _write_station(self, stop_places: ET.Element, station: dict,
                       layout: "_StationLayout", states_wheelchair: bool) -> None:
        """One GTFS station (location_type 1) as a StopPlace with its levels,
        entrances, platforms (Quays), generic nodes (AccessSpaces) and the
        pathways inside it (SitePathLinks), in the CEN StopPlace sequence."""
        profile = self.profile
        sid = station["id"]
        el = ET.SubElement(stop_places, "StopPlace", attrib=profile.entity_attrs(sid))
        if station.get("level_id"):
            # A StopPlace has no LevelRef (it is not a SiteComponent); GTFS lets
            # a station name one anyway, so it rides in the keyList.
            _write_key_values(el, {"gtfs_level_id": station["level_id"]})
        ET.SubElement(el, "Name").text = station["name"]
        _write_centroid(el, station)
        _write_accessibility(el, sid, station.get("wheelchair_boarding"))

        level_ids = layout.station_levels.get(sid, ())
        if level_ids:
            levels_el = ET.SubElement(el, "levels")
            for lid in level_ids:
                self._write_level(levels_el, layout.levels[lid],
                                  layout.level_ref(sid, lid))

        entrances = layout.children(sid, 2)
        if entrances:
            ent_el = ET.SubElement(el, "entrances")
            for area in entrances:
                ent = ET.SubElement(ent_el, "StopPlaceEntrance",
                                    attrib=profile.entity_attrs(area["id"]))
                self._write_component_body(ent, area, sid, layout)

        modes = {self._stop_modes.get(sp["id"]) for sp in layout.platforms(sid)}
        modes.discard(None)
        mode = (modes.pop() if len(modes) == 1
                else (self._default_stop_mode if not modes else None))
        ET.SubElement(el, "StopPlaceType").text = profile.stop_place_type(mode)

        platforms = layout.platforms(sid)
        if platforms:
            quays_el = ET.SubElement(el, "quays")
            for sp in platforms:
                wheelchair = sp.get("wheelchair_boarding") if states_wheelchair else None
                self._write_quay(quays_el, sp, wheelchair,
                                 layout.level_ref(sid, sp.get("level_id")),
                                 in_station=True)

        nodes = layout.children(sid, 3)
        if nodes:
            spaces_el = ET.SubElement(el, "accessSpaces")
            for area in nodes:
                space = ET.SubElement(spaces_el, "AccessSpace",
                                      attrib=profile.entity_attrs(area["id"]))
                self._write_component_body(space, area, sid, layout)

        pathways = layout.station_pathways.get(sid)
        if pathways:
            links_el = ET.SubElement(el, "pathLinks")
            for pw in pathways:
                self._write_path_link(links_el, "SitePathLink", pw, layout)

    def _write_component_body(self, el: ET.Element, area: dict, station_id: str,
                              layout: "_StationLayout") -> None:
        """Name, Centroid, AccessibilityAssessment and LevelRef: the part of a
        StopPlaceEntrance and an AccessSpace that comes from GTFS, in the order
        the two share."""
        ET.SubElement(el, "Name").text = area["name"]
        _write_centroid(el, area)
        _write_accessibility(el, area["id"], area.get("wheelchair_boarding"))
        ref = layout.level_ref(station_id, area.get("level_id"))
        if ref:
            ET.SubElement(el, "LevelRef", attrib=self.profile.ref_attrs(ref))

    def _write_level(self, parent: ET.Element, level: dict, level_id: str) -> None:
        """A GTFS level, copied into one station.

        NeTEx levels belong to a site and GTFS levels to the feed, so a level
        shared by several stations is written once per station under a derived
        id; the GTFS id travels in the keyList and the importer folds the
        copies back into one level.
        """
        lv = ET.SubElement(parent, "Level", attrib=self.profile.entity_attrs(level_id))
        index = level.get("index")
        keys = {"gtfs_level_id": level["id"]}
        integral = index is not None and float(index).is_integer()
        if index is not None and not integral:
            keys["gtfs_level_index"] = repr(float(index))
        _write_key_values(lv, keys)
        if level.get("name"):
            ET.SubElement(lv, "Name").text = level["name"]
        if integral:
            ET.SubElement(lv, "RelativeLevelOrder").text = str(int(float(index)))

    def _write_path_link(self, parent: ET.Element, tag: str, pw: dict,
                         layout: "_StationLayout") -> None:
        """A GTFS pathway as a (Site)PathLink, children in CEN order."""
        profile = self.profile
        link = ET.SubElement(parent, tag, attrib=profile.entity_attrs(pw["id"]))
        keys = {"gtfs_pathway_mode": str(pw["pathway_mode"])}
        if pw.get("max_slope") is not None:
            # Gradient's unit is not GTFS's ratio; the exact value goes here.
            keys["gtfs_max_slope"] = repr(float(pw["max_slope"]))
        _write_key_values(link, keys)
        if pw.get("length") is not None:
            ET.SubElement(link, "Distance").text = repr(float(pw["length"]))
        for end_tag, stop_id in (("From", pw["from_stop_id"]), ("To", pw["to_stop_id"])):
            end = ET.SubElement(link, end_tag)
            ET.SubElement(end, "PlaceRef",
                          attrib=profile.ref_attrs(layout.place_ref(stop_id)))
        if pw.get("signposted_as"):
            ET.SubElement(link, "Towards").text = pw["signposted_as"]
        if pw.get("reversed_signposted_as"):
            ET.SubElement(link, "Back").text = pw["reversed_signposted_as"]
        stairs = pw.get("stair_count")
        if stairs is not None:
            ET.SubElement(link, "NumberOfSteps").text = str(abs(int(stairs)))
        if pw.get("min_width") is not None:
            ET.SubElement(link, "MinimumWidth").text = repr(float(pw["min_width"]))
        ET.SubElement(link, "AllowedUse").text = (
            "twoWay" if pw["is_bidirectional"] else "oneWay")
        if stairs:
            # GTFS signs the count: positive climbs from `from` to `to`.
            ET.SubElement(link, "Transition").text = "up" if int(stairs) > 0 else "down"
        feature = _PATHWAY_ACCESS_FEATURE.get(int(pw["pathway_mode"]))
        if feature:
            ET.SubElement(link, "AccessFeatureType").text = feature
        if pw.get("traversal_time") is not None:
            duration = ET.SubElement(link, "TransferDuration")
            ET.SubElement(duration, "DefaultDuration").text = f"PT{int(pw['traversal_time'])}S"

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

        profile = self.profile
        frame = ET.SubElement(frames, "ServiceFrame",
                              attrib=profile.frame_attrs("ServiceFrame"))
        profile.write_frame_header(frame, "ServiceFrame", self._validity)

        if patterns and profile.writes_routes:
            # The PT profile has no JourneyPattern in any collection its
            # ServiceFrame admits, and its journeys point at a pattern only
            # through JourneyPatternView/RouteRef. The Route is therefore the
            # one object that can carry a pattern's identity, so one is minted
            # per pattern and keeps the pattern's own id. Re-importing recovers
            # the route -> line edge; it does not recover the stop sequence,
            # which this profile states nowhere (matrix D5).
            line_names = {ln["id"]: ln["name"] for ln in lines}
            routes_el = profile.container(frame, "routes")
            emitted = patterns
            if self._canonical_jps is not None:
                emitted = [jp for jp in patterns if jp["id"] in self._canonical_jps]
            for jp in emitted:
                route = ET.SubElement(routes_el, "Route",
                                      attrib=profile.entity_attrs(jp["id"]))
                ET.SubElement(route, "Name").text = line_names.get(
                    jp["line_id"], jp["line_id"])
                ET.SubElement(route, "LineRef",
                              attrib=profile.ref_attrs(jp["line_id"]))
                if jp.get("direction") and profile.states("journey_pattern", "direction"):
                    ET.SubElement(route, "DirectionType").text = jp["direction"]

        if lines:
            lines_el = profile.container(frame, "lines")
            for line in lines:
                el = ET.SubElement(lines_el, "Line",
                                   attrib=profile.entity_attrs(line["id"]))
                # CEN LineGroup sequence: Name, ShortName, Description,
                # TransportMode, TransportSubmode, Url, PublicCode,
                # PrivateCode, ..., AuthorityRef, OperatorRef. The short name
                # goes into the slot its element name has, which is not the
                # same slot for the two spellings -- writing PublicCode where
                # ShortName goes is rejected by the schema, and was, before
                # this was measured.
                ET.SubElement(el, "Name").text = line["name"]
                short_name = (line.get("short_name")
                              if profile.states("line", "short_name") else None)
                if short_name and profile.line_short_name_element == "ShortName":
                    ET.SubElement(el, "ShortName").text = short_name
                if line.get("description") and profile.states("line", "description"):
                    ET.SubElement(el, "Description").text = line["description"]
                ET.SubElement(el, "TransportMode").text = profile.vehicle_mode(
                    line["transport_mode"])
                if line.get("url") and profile.states("line", "url"):
                    ET.SubElement(el, "Url").text = line["url"]
                if short_name and profile.line_short_name_element == "PublicCode":
                    ET.SubElement(el, "PublicCode").text = short_name
                if line.get("operator_id"):
                    ET.SubElement(el, profile.line_operator_ref,
                                  attrib=profile.ref_attrs(line["operator_id"]))
                # Presentation comes *after* the authority reference: CEN puts
                # it in LinePropertiesGroup, not next to Name where the two
                # colours read as though they belonged.
                self._write_line_presentation(el, line)

        if stops:
            ssps_el = profile.container(frame, "scheduledStopPoints")
            for sp in stops:
                el = ET.SubElement(ssps_el, "ScheduledStopPoint",
                                   attrib=profile.entity_attrs(sp["id"]))
                ET.SubElement(el, "Name").text = sp["name"]
                if profile.writes_stop_point_location:
                    # The PT profile puts the coordinates on the stop point
                    # itself and makes StopType mandatory (1:1). Under EPIP the
                    # point is abstract and the geometry lives on the StopPlace.
                    if sp.get("lat") is not None and sp.get("lon") is not None:
                        loc = ET.SubElement(el, "Location")
                        ET.SubElement(loc, "Longitude").text = str(sp["lon"])
                        ET.SubElement(loc, "Latitude").text = str(sp["lat"])
                    if sp.get("code"):
                        ET.SubElement(el, "PrivateCode").text = sp["code"]
                    ET.SubElement(el, "StopType").text = self._stop_type(sp["id"])

            if profile.writes_stop_assignments:
                # Bind each ScheduledStopPoint to its Quay. This was a QuayRef
                # *attribute* on ScheduledStopPoint until 2026-09-07 — convenient
                # for third-party loaders but not legal NeTEx: QuayRef is an
                # element, and ScheduledStopPoint admits it in neither form.
                # PassengerStopAssignment is the conformant binding, and the one
                # our own importer already reads (netex_importer.on_assignment).
                assignments_el = profile.container(frame, "stopAssignments")
                for order, sp in enumerate(stops, start=1):
                    psa = ET.SubElement(
                        assignments_el, "PassengerStopAssignment",
                        attrib={**profile.entity_attrs(f"{sp['id']}:PSA"),
                                "order": str(order)})
                    ET.SubElement(psa, "ScheduledStopPointRef",
                                  attrib=profile.ref_attrs(sp["id"]))
                    ET.SubElement(psa, "QuayRef",
                                  attrib=profile.ref_attrs(f"{sp['id']}:Quay"))

        if patterns and not profile.writes_journey_patterns:
            # Counted, not silently dropped: a caller who exports a patterned
            # network into a profile that cannot hold patterns has lost the
            # stop sequences, and is entitled to a number for it.
            profile.note_unsupported("journey_pattern", len(patterns))
            profile.note_unsupported(
                "point_in_journey_pattern",
                sum(1 for _ in points),
            )
        if not profile.writes_service_links:
            # Geometry rides on ServiceLinks referenced from a JourneyPattern.
            # A profile with neither has nowhere to put a shape at all.
            profile.note_unsupported("shape",
                                     db.count_distinct("shape_point", "shape_id"))
        if patterns and profile.writes_journey_patterns:
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
                if jp.get("direction") and profile.states("journey_pattern", "direction"):
                    ET.SubElement(el, "DirectionType").text = jp["direction"]

                pattern_pts = points_by_pattern.get(jp["id"], [])
                if len(pattern_pts) == 1:
                    # CEN gives pointsInJourneyPattern_RelStructure a
                    # `minOccurs="2"` choice: a pattern either sequences two
                    # points or sequences none. Writing the single point made
                    # the whole document schema-invalid -- a journey has to go
                    # from somewhere to somewhere -- so it is left out and
                    # counted, and the journeys on it lose their anchors the
                    # same way any other unanchored passing time does.
                    self._degenerate_jps.add(jp["id"])
                    profile.note_dropped("point_in_journey_pattern_single",
                                         len(pattern_pts))
                    pattern_pts = []
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
        if transfers and not profile.writes_connections:
            profile.note_unsupported("transfer", len(transfers))
            transfers = []
        if transfers:
            # A Connection joins two ScheduledStopPoints. A GTFS transfer may
            # name a station, which this exporter does not write as a stop
            # point, so such a transfer has nothing to reference.
            point_ids = {r["id"] for r in db.get_records("scheduled_stop_point")}
            kept = [t for t in transfers
                    if t["from_stop_id"] in point_ids and t["to_stop_id"] in point_ids]
            profile.note_unsupported("transfer_station_end", len(transfers) - len(kept))
            transfers = kept
        if transfers:
            connections = profile.container(frame, "connections")
            for i, tr in enumerate(transfers, 1):
                conn = ET.SubElement(
                    connections, "Connection",
                    attrib=profile.entity_attrs(profile.object_id("Connection", i)))
                if tr.get("transfer_type") and profile.states("transfer", "transfer_type"):
                    key_list = ET.SubElement(conn, "keyList")
                    kv = ET.SubElement(key_list, "KeyValue")
                    ET.SubElement(kv, "Key").text = "gtfs_transfer_type"
                    ET.SubElement(kv, "Value").text = str(tr["transfer_type"])
                if (tr.get("min_transfer_time") is not None
                        and profile.states("transfer", "min_transfer_time")):
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

    def _write_line_presentation(self, el: ET.Element, line: dict) -> None:
        """`Line/Presentation` — the two colours GTFS calls route_color and
        route_text_color.

        CEN names them `Colour` and `TextColour` and types them hexBinary, which
        is not the `ColourCode`/`TextColourCode` the first reading of the profile
        assumed: the XSD settles it. A value that is not six hex digits is not
        written and is counted, because one malformed colour would otherwise
        fail the whole document at the schema rather than one Line.
        """
        profile = self.profile
        usable = []
        for field, tag in (("color", "Colour"), ("text_color", "TextColour")):
            raw = line.get(field)
            if not raw or not profile.states("line", field):
                continue
            value = _colour(raw)
            if value is None:
                profile.note_dropped(f"line_{field}_not_hex")
                continue
            usable.append((tag, value))
        if not usable:
            return
        presentation = ET.SubElement(el, "Presentation")
        for tag, value in usable:
            ET.SubElement(presentation, tag).text = value

    # -------------------------------------------------------------------------
    # ServiceCalendarFrame — day types
    # -------------------------------------------------------------------------

    def _write_service_calendar_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        day_types = db.get_records("day_type")
        if not day_types:
            return
        exceptions = db.get_records("operating_day_exception")

        if self.profile.writes_per_day_type_calendars:
            self._write_per_day_type_calendars(day_types, exceptions, frames)
            return

        profile = self.profile
        frame = ET.SubElement(frames, "ServiceCalendarFrame",
                              attrib=profile.frame_attrs("ServiceCalendarFrame"))
        profile.write_frame_header(frame, "ServiceCalendarFrame", self._validity)
        dt_container = profile.container(frame, "dayTypes")
        for dt in day_types:
            el = ET.SubElement(dt_container, "DayType",
                               attrib=profile.entity_attrs(dt["id"]))
            valid_between = ET.SubElement(el, "ValidBetween")
            ET.SubElement(valid_between, "FromDate").text = f"{_iso_date(dt['start_date'])}T00:00:00"
            ET.SubElement(valid_between, "ToDate").text = f"{_iso_date(dt['end_date'])}T23:59:59"
            self._write_days_of_week(el, dt)

        if exceptions:
            assignments = profile.container(frame, "dayTypeAssignments")
            for i, exc in enumerate(exceptions, 1):
                a = ET.SubElement(
                    assignments, "DayTypeAssignment",
                    attrib={**profile.entity_attrs(
                        profile.object_id("DayTypeAssignment", i)),
                        "order": str(i)})
                ET.SubElement(a, "Date").text = _iso_date(exc["date"])
                ET.SubElement(a, "DayTypeRef",
                              attrib=profile.ref_attrs(exc["day_type_id"]))
                ET.SubElement(a, "isAvailable").text = (
                    "true" if exc["is_addition"] else "false"
                )

    @staticmethod
    def _write_days_of_week(day_type_el: ET.Element, dt: dict) -> None:
        active = " ".join(name for flag, name in DAY_FLAG_TO_NAME if dt.get(flag))
        if active:
            pod = ET.SubElement(ET.SubElement(day_type_el, "properties"),
                                "PropertyOfDay")
            ET.SubElement(pod, "DaysOfWeek").text = active

    def _write_per_day_type_calendars(self, day_types: list, exceptions: list,
                                      frames: ET.Element) -> None:
        """One ServiceCalendarFrame per day type — the PT profile's shape.

        Not a stylistic choice. The PT profile has no DayTypeAssignment, and its
        AvailabilityCondition names no day type: the *only* thing that
        associates an exception date with a day type is the
        ServiceCalendarFrame that contains both. One frame per day type is
        therefore the sole shape in which the exceptions survive being read
        back — and it is the shape ``PortugueseAdapter.resolve_calendar``
        recognises, which is what makes the round trip close.

        The window goes on the ServiceCalendar rather than on the DayType,
        because this profile's DayType has no ValidBetween (spec-notas §4.1).
        """
        profile = self.profile
        by_day_type: Dict[str, list] = {}
        for exc in exceptions:
            by_day_type.setdefault(exc["day_type_id"], []).append(exc)

        for seq, dt in enumerate(day_types, start=1):
            frame = ET.SubElement(
                frames, "ServiceCalendarFrame",
                attrib=profile.frame_attrs("ServiceCalendarFrame", seq))
            profile.write_frame_header(frame, "ServiceCalendarFrame", self._validity)

            dt_exceptions = by_day_type.get(dt["id"], [])
            if dt_exceptions:
                # contentValidityConditions before ServiceCalendar before
                # dayTypes: the CEN VersionFrameValiditiesGroup precedes the
                # frame's own group, and it is the order the profile publishes.
                conditions = profile.container(frame, "contentValidityConditions")
                for exc in dt_exceptions:
                    iso = _iso_date(exc["date"])
                    ac = ET.SubElement(
                        conditions, "AvailabilityCondition",
                        attrib=profile.calendar_entity_attrs(f"{dt['id']}:{exc['date']}"))
                    # A single-date exception is a window of one day. Reading it
                    # back needs FromDate == ToDate, which is what tells the
                    # importer this is an exception and not a validity period.
                    ET.SubElement(ac, "FromDate").text = f"{iso}T00:00:00"
                    ET.SubElement(ac, "ToDate").text = f"{iso}T23:59:59"
                    ET.SubElement(ac, "IsAvailable").text = (
                        "true" if exc["is_addition"] else "false")

            calendar = ET.SubElement(
                frame, "ServiceCalendar",
                attrib=profile.calendar_entity_attrs(dt["id"]))
            ET.SubElement(calendar, "FromDate").text = _iso_date(dt["start_date"])
            ET.SubElement(calendar, "ToDate").text = _iso_date(dt["end_date"])

            dt_container = profile.container(frame, "dayTypes")
            el = ET.SubElement(dt_container, "DayType",
                               attrib=profile.entity_attrs(dt["id"]))
            ET.SubElement(el, "Name").text = dt["id"]
            self._write_days_of_week(el, dt)

    # -------------------------------------------------------------------------
    # TimetableFrame — service journeys, passing times
    # -------------------------------------------------------------------------

    def _write_timetable_frame(self, db: TransmodelDatabase, frames: ET.Element) -> None:
        journeys = db.get_records("service_journey")
        passing_times = db.get_records("passing_time")

        if not journeys:
            return

        profile = self.profile
        frame = ET.SubElement(frames, "TimetableFrame",
                              attrib=profile.frame_attrs("TimetableFrame"))
        profile.write_frame_header(frame, "TimetableFrame", self._validity)
        if profile.write_prerequisites:
            # The timetable depends on the ServiceFrame's lines and stop points.
            # The PT example states it; nothing reads it, but a frame that
            # declares its prerequisites can be validated in isolation.
            prereq = profile.container(frame, "prerequisites")
            ET.SubElement(prereq, "ServiceFrameRef", attrib={
                "ref": profile.frame_id("ServiceFrame"),
                "version": profile.frame_version,
            })

        pt_by_journey: dict = {}
        for pt in passing_times:
            pt_by_journey.setdefault(pt["service_journey_id"], []).append(pt)
        for pts in pt_by_journey.values():
            pts.sort(key=lambda p: p["order"])

        # Ordered (StopPointInJourneyPattern id, stop) per pattern, so a
        # journey's k-th passing time can reference the pattern's k-th point.
        # Matching positionally (not by the passing time's own `order` value)
        # keeps the refs valid when the two order columns use different bases —
        # but position alone is not identity, which is why the stop travels
        # with the id and is checked before the ref is written.
        spi_ids_by_pattern: dict = {}
        for pt in db.get_records("point_in_journey_pattern"):
            spi_ids_by_pattern.setdefault(pt["journey_pattern_id"], []).append(
                (pt["order"], f"{pt['journey_pattern_id']}_stop_{pt['order']}",
                 pt["stop_point_id"]))
        for entries in spi_ids_by_pattern.values():
            entries.sort()
        # A pattern whose points the ServiceFrame could not sequence has no
        # StopPointInJourneyPattern to point at; referencing one would dangle.
        for jp_id in self._degenerate_jps:
            spi_ids_by_pattern.pop(jp_id, None)

        freq_by_journey: dict = {}
        for fr in db.get_records("frequency"):
            freq_by_journey.setdefault(fr["service_journey_id"], []).append(fr)

        if journeys:
            directions = {jp["id"]: jp.get("direction")
                          for jp in db.get_records("journey_pattern")}
            journeys_el = profile.container(frame, "vehicleJourneys")
            for sj in journeys:
                # Frequency-based journeys are TemplateServiceJourneys (the
                # ServiceJourney subtype that carries frequency groups).
                frequencies = freq_by_journey.get(sj["id"], [])
                if frequencies and profile.stop_times_element == "calls":
                    # HeadwayJourneyGroup hangs off a TemplateServiceJourney,
                    # which the PT profile does not list. The journey is still
                    # written, at its first departure; the headway window is not.
                    profile.note_unsupported("frequency", len(frequencies))
                    frequencies = []
                tag = "TemplateServiceJourney" if frequencies else "ServiceJourney"
                el = ET.SubElement(journeys_el, tag,
                                   attrib=profile.entity_attrs(sj["id"]))
                jp_id = sj.get("journey_pattern_id")
                if jp_id and self._jp_remap:
                    jp_id = self._jp_remap.get(jp_id, jp_id)

                if profile.stop_times_element == "calls":
                    # The PT journey states its times only inside the calls, so
                    # DepartureTime is not written; the importer takes the first
                    # call's departure. CEN order for the rest: dayTypes,
                    # LineRef, DirectionType, JourneyPatternView, calls.
                    self._write_journey_head_pt(el, sj, jp_id, directions)
                    self._write_calls(el, pt_by_journey.get(sj["id"], []))
                    continue

                _write_accessibility(
                    el, sj["id"],
                    sj.get("wheelchair_accessible")
                    if profile.states("service_journey", "wheelchair_accessible")
                    else None)
                dep_time, dep_offset = _normalize_time(sj["departure_time"])
                ET.SubElement(el, "DepartureTime").text = dep_time
                if dep_offset:
                    ET.SubElement(el, "DepartureDayOffset").text = str(dep_offset)
                day_types_el = profile.container(el, "dayTypes")
                ET.SubElement(day_types_el, "DayTypeRef",
                              attrib=profile.ref_attrs(sj["day_type_id"]))
                if jp_id:
                    ET.SubElement(el, "JourneyPatternRef",
                                  attrib=profile.ref_attrs(jp_id))
                ET.SubElement(el, "LineRef",
                              attrib=profile.ref_attrs(sj["line_id"]))

                sj_pts = pt_by_journey.get(sj["id"], [])
                if sj_pts:
                    spi_points = spi_ids_by_pattern.get(jp_id, []) if jp_id else []
                    # Created on the first passing time that has somewhere to
                    # point, so a journey whose times all fall outside its
                    # pattern gets no empty <passingTimes> container.
                    pt_el = None
                    unanchored = 0
                    misplaced = 0
                    for k, pt in enumerate(sj_pts):
                        if k >= len(spi_points):
                            # A passing time at a position the journey's pattern
                            # has no point for. It used to be written anyway, as
                            # a TimetabledPassingTime referencing no stop at all
                            # — schema-valid, unreadable, and silent (achado 5).
                            # These are the V17 violators the dedup pass already
                            # warns about; the warning stays and the count is
                            # now in the return value beside it.
                            unanchored += 1
                            continue
                        _order, spi_id, spi_stop = spi_points[k]
                        if pt["stop_point_id"] != spi_stop:
                            # The same position in the two sequences, a
                            # different stop. A CEN TimetabledPassingTime names
                            # no stop of its own — it says only "the k-th point
                            # of the pattern" — so writing this one would move
                            # the time onto a stop the journey does not serve
                            # and produce a timetable that is false rather than
                            # incomplete. Dropped and counted instead, on the
                            # same rule as the unanchored case above.
                            #
                            # Not reachable for a journey that follows its
                            # pattern, dedup or no dedup: the §2 quotient groups
                            # by the ordered stop sequence, so a canonical
                            # pattern serves the stops its members serve.
                            misplaced += 1
                            continue
                        if pt_el is None:
                            pt_el = profile.container(el, "passingTimes")
                        tpt = ET.SubElement(pt_el, "TimetabledPassingTime",
                                            attrib=profile.passing_time_attrs())
                        ET.SubElement(tpt, "StopPointInJourneyPatternRef",
                                      attrib=profile.ref_attrs(spi_id))
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
                    profile.note_dropped("passing_time_no_pattern_point", unanchored)
                    profile.note_dropped("passing_time_stop_mismatch", misplaced)

                # After passingTimes: a (Template)ServiceJourney's children are a
                # schema sequence, and frequencyGroups follows passingTimes in it.
                if frequencies:
                    groups_el = profile.container(el, "frequencyGroups")
                    for i, fr in enumerate(frequencies, 1):
                        hjg = ET.SubElement(
                            groups_el, "HeadwayJourneyGroup",
                            attrib=profile.entity_attrs(f"{sj['id']}:hjg:{i}"))
                        if fr.get("exact_times") and profile.states("frequency", "exact_times"):
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

    # -------------------------------------------------------------------------
    # The calls-shaped journey (Portuguese profile)
    # -------------------------------------------------------------------------

    def _write_journey_head_pt(self, el: ET.Element, sj: dict,
                               jp_id, directions: dict) -> None:
        """dayTypes, LineRef, DirectionType, JourneyPatternView — in CEN order.

        ``dayTypes`` belongs to the VehicleJourneyReferencesGroup and the other
        three to the ServiceJourneyReferencesGroup, which follows it; within
        that group the sequence is LineRef, DirectionType, JourneyPatternView.
        The published PT example writes them in exactly this order, which is
        the cheapest available confirmation that the two agree.

        The ``dayTypes`` reference is what the CARRIS file omits in all 10 238
        of its journeys, forcing a positional pairing on anyone reading it
        (spec-notas §6c). We hold the key, so we write it.
        """
        profile = self.profile
        day_types_el = profile.container(el, "dayTypes")
        ET.SubElement(day_types_el, "DayTypeRef",
                      attrib={"ref": sj["day_type_id"]})
        ET.SubElement(el, "LineRef", attrib={"ref": sj["line_id"]})
        direction = directions.get(jp_id) if jp_id else None
        if direction and profile.writes_journey_direction:
            ET.SubElement(el, "DirectionType").text = direction
        if jp_id and profile.writes_journey_pattern_view:
            # The Route minted for this pattern in the ServiceFrame; the only
            # handle on a pattern this profile has.
            view = ET.SubElement(el, "JourneyPatternView")
            ET.SubElement(view, "RouteRef", attrib={"ref": jp_id})

    def _write_calls(self, el: ET.Element, sj_pts: list) -> None:
        """The journey's stop times as `calls`, not `passingTimes`.

        `ForAlighting` and `ForBoarding` are 1:1 in the profile and carry real
        information we hold: GTFS drop_off_type / pickup_type, where 0 or
        absent means allowed and anything else means it is not. Writing a
        constant `true` would have been easier and would have thrown the
        distinction away.

        `DayOffset` is written for service past midnight even though the
        profile's tables do not list it (spec-notas §6d): without it a 25:10
        departure is unrepresentable, and the file the IMT publishes uses it in
        all 296 603 of its calls.
        """
        profile = self.profile
        if len(sj_pts) < 2:
            # `calls` is the second of the exactly two containers this exporter
            # writes whose CEN type demands at least two children (the other is
            # `pointsInSequence`); both were audited against the vendored XSD
            # rather than found one at a time. A journey that calls once is not
            # a journey, and writing the single Call made the whole document
            # invalid, so it is left out and counted.
            profile.note_dropped("call_single_journey", len(sj_pts))
            return
        calls_el = profile.container(el, "calls")
        for order, pt in enumerate(sj_pts, start=1):
            call = ET.SubElement(
                calls_el, "Call",
                attrib={**profile.entity_attrs(
                    f"{pt['service_journey_id']}:call:{order}"),
                    "order": str(order)})
            ET.SubElement(call, "ScheduledStopPointRef",
                          attrib={"ref": pt["stop_point_id"]})
            self._write_call_part(call, "Arrival", pt.get("arrival_time"),
                                  "ForAlighting", pt.get("drop_off_type"))
            self._write_call_part(call, "Departure", pt.get("departure_time"),
                                  "ForBoarding", pt.get("pickup_type"))

    @staticmethod
    def _write_call_part(call: ET.Element, tag: str, time_value,
                         allow_tag: str, gtfs_type) -> None:
        """One Arrival or Departure: Time, DayOffset, then the boarding flag.

        The CEN ArrivalStructure/DepartureStructure sequence is exactly
        ``Time, DayOffset, ForAlighting|ForBoarding``, so this writes them in
        that order and nothing sorts afterwards.
        """
        if not time_value:
            return
        part = ET.SubElement(call, tag)
        value, offset = _normalize_time(time_value)
        ET.SubElement(part, "Time").text = value
        if offset:
            ET.SubElement(part, "DayOffset").text = str(offset)
        ET.SubElement(part, allow_tag).text = (
            "false" if gtfs_type not in (None, 0) else "true")
