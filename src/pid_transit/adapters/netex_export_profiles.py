"""What a NeTEx profile wants written differently, in one place.

The mirror image of ``netex_profiles``: that module says how to *read* a
dialect, this one says how to *write* one. Same rule governs both -- a profile
is a subclass, never an ``if profile == ...`` inside a writer the other
profiles run through.

The asymmetry with the importer is deliberate and worth naming. An importer's
base class can answer "nothing to do" to every hook, because reading nothing is
always safe. An exporter's base class has to answer *something*: some element
gets written. So the base here is the EPIP reference implementation, and
``EpipProfile`` is the empty subclass that names it. A new profile still cannot
change what the existing ones emit -- it can only override its own answers --
which is the same safety property, obtained the only way an exporter allows.

Losses
------
A profile that cannot carry something does not quietly drop it. Every profile
counts what it had to leave out under an ``unsupported_`` key, on the same
principle as the importer's ``skipped_``/``dropped_`` counters: a caller must be
able to ask "what did this export not say?" and get a number. Zero adds no key.

The rule holds for a *field* as much as for an entity. Until 2026-09-10 it did
not: ``unsupported_`` counted only entities a profile had no element for, so an
EPIP export that silently wrote no timezone, no line colour and no feed_info
returned ``{}`` and claimed a fidelity it did not have. Which fields a profile's
document actually states is now declared in ``WRITTEN_FIELDS``, and the exporter
counts every model field outside it -- derived from ``schemas.py`` at export
time, so a column added to the model and to no writer surfaces as a loss
instead of disappearing.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, FrozenSet, Optional, Sequence, Tuple

# Prefixes for the loss counters an export reports, matching the importer's
# LOSS_PREFIXES so a caller can filter both the same way.
#
# Two prefixes, because they are two different statements. `unsupported_` means
# the profile has no slot for the thing at all -- a PT export of a patterned
# network. `dropped_` means the writer had a slot and this particular datum did
# not fit it -- a passing time at a position its journey pattern has no point
# for. The first is answered by choosing another profile, the second by fixing
# the feed, so collapsing them into one key would hide which question to ask.
UNSUPPORTED_PREFIX = "unsupported_"
DROPPED_PREFIX = "dropped_"
#: Field losses sit one level deeper, so "this profile carries no journey
#: patterns" (220 entities) stays tellable from "this profile carries no line
#: colour" (71 fields) without parsing the rest of the key.
FIELD_PREFIX = UNSUPPORTED_PREFIX + "field_"

# Internal TransportMode values whose NeTEx VehicleMode spelling differs.
# Values not listed here (bus, tram, rail, metro, ferry, funicular, monorail)
# are already valid NeTEx mode names and pass through unchanged.
_INTERNAL_MODE_TO_NETEX = {
    "trolleybus": "trolleyBus",
    "cable_car": "cableway",
    "gondola": "telecabin",
    "other": "unknown",
}


def netex_mode(mode: str) -> str:
    """Internal transport mode -> the NeTEx VehicleMode spelling."""
    return _INTERNAL_MODE_TO_NETEX.get(mode, mode)


# Internal TransportMode -> CEN StopTypeEnumeration, verified value by value
# against the vendored XSD (netex_mode_support.xsd). The modes with no honest
# answer in that enumeration -- monorail, and `other` itself -- are absent and
# fall through to "other" rather than being rounded to the nearest rail word.
_MODE_TO_STOP_TYPE = {
    "bus": "onstreetBus",
    "trolleybus": "onstreetBus",
    "tram": "onstreetTram",
    "cable_car": "onstreetTram",   # GTFS route_type 5 is a street-level cable tram
    "rail": "railStation",
    "metro": "metroStation",
    "ferry": "ferryStop",
    "funicular": "liftStation",
    "gondola": "liftStation",
}


class ExportProfile:
    """The EPIP reference implementation, and the base every profile restricts.

    EPIP is CEN/TS 16614's European Passenger Information Profile -- the one the
    Delegated Regulation 2017/1926 points at. The defaults below are what this
    library emitted before profiles existed, so ``NetexExporter()`` with no
    argument writes exactly the same document it always did. That equivalence is
    a test (``TestProfileMechanismIsInert``), not a hope: it is what makes the
    profile parameter safe to introduce into a released exporter.
    """

    name = "epip"
    description = "CEN/TS 16614 European Passenger Information Profile"

    # -- document and frame policy -----------------------------------------
    #: Codespace to declare on the CompositeFrame, or None to declare none.
    #: Same string as `id_prefix`, because it names the same thing: the
    #: authority the ids in this document belong to. `DefaultCodespaceRef` then
    #: says which codespace to assume for an id that states none, which is
    #: every id this exporter passes through from the source feed.
    codespace: Optional[str] = "PID"
    #: Prefix for the ids this exporter mints for frames and synthetic objects.
    id_prefix = "PID"
    #: `version` attribute for frames and for entities. The PT examples use
    #: different values for the two ("1.0" and "any"), so they are separate.
    frame_version = "1"
    entity_version = "1"
    #: `modification` attribute, or None to omit it.
    modification: Optional[str] = None
    #: `modificationSet` on 1:N containers, or None to omit it.
    modification_set: Optional[str] = None

    #: `FrameDefaults` on the CompositeFrame. EPIP asks for it, and it is the
    #: only place a NeTEx document states its timezone -- every time in the file
    #: is local to it. Off until 2026-09-10, which left a reader no choice but
    #: to assume UTC and shifted the whole timetable by an hour (auditoria,
    #: achados 1+2). The defaults below are the values EPIP specifies.
    write_frame_defaults = True
    default_location_system: Optional[str] = "EPSG:4326"
    default_system_of_units: Optional[str] = "SiMetres"
    #: A frame that does not say which profile it belongs to leaves a consumer
    #: guessing, and `TypeOfFrameRef` is how EPIP is declared -- so the
    #: reference implementation writes it, and `TYPE_OF_FRAME` below stops
    #: being a table nothing reads. `ValidBetween` likewise: EPIP asks for one
    #: on each frame, and `_feed_validity` was already computing the window and
    #: throwing it away. Both were `False` on the base until 2026-09-10, which
    #: made the profile named EPIP the one document that never claimed to be.
    write_type_of_frame = True
    write_frame_valid_between = True
    write_prerequisites = False

    # -- content policy ----------------------------------------------------
    #: Emit a `routes` collection. The PT profile has no JourneyPattern element,
    #: so the Route is the only thing a journey can point its pattern view at.
    writes_routes = False
    writes_journey_patterns = True
    writes_quays = True
    #: Stations, entrances, generic nodes, levels and pathways as the CEN
    #: StopPlace structure (StopPlace > levels/entrances/quays/accessSpaces/
    #: pathLinks). Needs Quays, because a station's platforms are its Quays.
    #: Strictly this is more than EPIP's minimal stop model -- the detailed
    #: structure is EPIAP's (CEN/TS 16614-6) -- but every element is CEN-valid,
    #: and without it a GTFS station feed cannot survive the round trip.
    writes_station_structure = True
    writes_stop_assignments = True
    writes_service_links = True
    writes_connections = True
    #: One ServiceCalendarFrame per day type, holding that day type's own
    #: AvailabilityConditions, versus one frame with dayTypeAssignments.
    writes_per_day_type_calendars = False
    #: Coordinates and a StopType on the ScheduledStopPoint itself, not only on
    #: the StopPlace.
    writes_stop_point_location = False
    writes_journey_direction = False
    writes_journey_pattern_view = False
    organisation_element = "Operator"
    line_operator_ref = "OperatorRef"
    #: "ShortName" or "PublicCode" -- the same datum, and the CEN Line admits
    #: both, but in *different slots*: ShortName sits second, PublicCode sits
    #: after TransportMode and Url. Choosing the name therefore chooses the
    #: position too, which is why the writer places it by name rather than
    #: writing it wherever it is convenient.
    line_short_name_element = "ShortName"
    #: "passingTimes" (TimetabledPassingTime) or "calls" (Call).
    stop_times_element = "passingTimes"

    # -- the EPIP XSD's narrower shapes ------------------------------------
    #: The EPIP XSD (github.com/NeTEx-CEN/NeTEx-Profile-EPIP) is a subset of
    #: the CEN schema, and in several places it admits only one of the forms
    #: CEN allows. The base keeps the CEN form this exporter always wrote, so
    #: the profiles that inherit it (PT) do not move; `EpipProfile` switches
    #: each one to the form the EPIP XSD admits, and every switch is also
    #: valid CEN. Measured: the 0.7.4 EPIP export of Porto failed the EPIP
    #: XSD with 24 969 errors, all from these families.
    #:
    #: "JourneyPattern" or "ServiceJourneyPattern" -- EPIP lists only the
    #: subtype, and a journey then references it as ServiceJourneyPatternRef.
    journey_pattern_element = "JourneyPattern"
    #: The pattern names its line through RouteView/LineRef. EPIP requires a
    #: RouteRef or a RouteView on every pattern and has no LineRef on the
    #: journey, so this is the only place the journey's line can be stated.
    pattern_line_as_route_view = False
    #: The pattern's direction as a DirectionRef to a Direction in the
    #: ServiceFrame's `directions`, instead of a DirectionType child that the
    #: EPIP pattern does not have.
    pattern_direction_as_ref = False
    #: A pattern's ServiceLinks named one per stop, as the onward link of
    #: each StopPointInJourneyPattern, instead of a linksInSequence the EPIP
    #: pattern does not have.
    pattern_links_as_onward_refs = False
    #: LineRef on the ServiceJourney. EPIP's ServiceJourney has none.
    journey_writes_line_ref = True
    #: The journey's wheelchair flag in its keyList. EPIP's ServiceJourney has
    #: no AccessibilityAssessment, and the flag would otherwise be lost.
    journey_accessibility_in_key_list = False
    #: The hop's geometry inside projections/LinkSequenceProjection, or as a
    #: gml:LineString directly on the ServiceLink (the only form EPIP admits).
    service_link_projection = True
    #: A PassengerStopAssignment that names the StopPlace before the Quay.
    #: EPIP makes StopPlaceRef mandatory there.
    assignment_writes_stop_place_ref = False
    #: EPIP makes ContactDetails and OrganisationType mandatory on an Operator.
    operator_contact_and_type_required = False
    #: Day types and their assignments inside one ServiceCalendar. EPIP's
    #: ServiceCalendarFrame admits nothing else.
    calendar_in_service_calendar = False

    #: Closed enumerations, or None for "the profile does not restrict this".
    #: A value outside the set is written anyway and counted -- see
    #: `vehicle_mode`.
    vehicle_modes: Optional[frozenset] = None
    stop_place_types: Optional[frozenset] = None

    #: TypeOfFrame refs, by frame kind. EPIP names are the CEN ones.
    TYPE_OF_FRAME = {
        "ResourceFrame": "epip:EU_PI_COMMON",
        "SiteFrame": "epip:EU_PI_STOP",
        "ServiceFrame": "epip:EU_PI_NETWORK",
        "ServiceCalendarFrame": "epip:EU_PI_CALENDAR",
        "TimetableFrame": "epip:EU_PI_TIMETABLE",
        "CompositeFrame": "epip:EU_PI_COMMON",
    }

    #: Which of each entity's model fields this profile's document actually
    #: states. It is both the gate the writers ask before writing an optional
    #: field (`states`) and the ledger the losses are computed from
    #: (`unwritten_fields`) -- one declaration, so a profile cannot turn a field
    #: off in one place and go on claiming it in the other. It replaced five
    #: separate `writes_<field>` booleans on 2026-09-10, which is exactly the
    #: pair that had drifted: the booleans were off and nothing counted them.
    #:
    #: Three things are deliberate here. It lists what *is* written, not what is
    #: lost, because the writers are what this file can be checked against and
    #: the model is not: a column added to `schemas.py` and to no writer then
    #: shows up as a loss by default instead of being forgotten. An entity
    #: missing from the map altogether is one no writer emits at all, counted
    #: whole rather than field by field. And an entity the *profile* cannot
    #: carry stays in the map with its fields listed: the writer already counts
    #: that entity whole (`unsupported_journey_pattern`), and counting its
    #: columns again would report the same loss twice in two shapes.
    #:
    #: `extras` never appears, and is never counted: it is itself the record of
    #: what an importer could not map, so an export loss for it would be the
    #: same loss told a second time.
    WRITTEN_FIELDS: Dict[str, FrozenSet[str]] = {
        # timezone and lang are document-level, in FrameDefaults/DefaultLocale;
        # operators that disagree with the one written are counted by the writer.
        "operator": frozenset({"id", "name", "url", "timezone", "lang", "phone"}),
        "line": frozenset({"id", "operator_id", "name", "short_name",
                           "transport_mode", "color", "text_color",
                           "description", "url"}),
        # stop_area_id and level_id are stated by containment and LevelRef in
        # the station's StopPlace; platform_code is the Quay's PublicCode.
        "scheduled_stop_point": frozenset({"id", "name", "lat", "lon",
                                           "wheelchair_boarding", "code",
                                           "stop_area_id", "level_id",
                                           "platform_code"}),
        # The station structure (writes_station_structure). A record the
        # writer cannot place -- a boarding area, an entrance with no station
        # -- is counted whole by the writer, not here.
        "stop_area": frozenset({"id", "name", "lat", "lon", "location_type",
                                "parent_id", "level_id",
                                "wheelchair_boarding"}),
        "level": frozenset({"id", "index", "name"}),
        "pathway": frozenset({"id", "from_stop_id", "to_stop_id",
                              "pathway_mode", "is_bidirectional", "length",
                              "traversal_time", "stair_count", "max_slope",
                              "min_width", "signposted_as",
                              "reversed_signposted_as"}),
        "day_type": frozenset({"id", "monday", "tuesday", "wednesday",
                               "thursday", "friday", "saturday", "sunday",
                               "start_date", "end_date"}),
        "operating_day_exception": frozenset({"day_type_id", "date",
                                              "is_addition"}),
        # `line_id` is not on the JourneyPattern element and does not need to
        # be: every journey names both, so the edge is stated by the document
        # and comes back on re-import. Listed as written because it is not lost.
        "journey_pattern": frozenset({"id", "line_id", "direction"}),
        "point_in_journey_pattern": frozenset({"journey_pattern_id",
                                               "stop_point_id", "order"}),
        "service_journey": frozenset({"id", "line_id", "journey_pattern_id",
                                      "day_type_id", "departure_time",
                                      "wheelchair_accessible", "shape_id"}),
        # `stop_point_id` rides on StopPointInJourneyPatternRef, which resolves
        # through the pattern. pickup/drop_off have no slot in a CEN
        # TimetabledPassingTime -- the calls form carries them, EPIP does not.
        "passing_time": frozenset({"service_journey_id", "stop_point_id",
                                   "order", "arrival_time", "departure_time"}),
        "feed_info": frozenset({"id", "publisher_name", "publisher_url", "lang",
                                "start_date", "end_date", "version",
                                "contact_email", "contact_url"}),
        "shape_point": frozenset({"shape_id", "lat", "lon", "sequence"}),
        "frequency": frozenset({"service_journey_id", "start_time", "end_time",
                                "headway_secs", "exact_times"}),
        "transfer": frozenset({"from_stop_id", "to_stop_id", "transfer_type",
                               "min_transfer_time"}),
    }

    def __init__(self) -> None:
        # Per-run counters, reset by `begin_run`. One profile instance per
        # export is the usual shape, like the importer's one adapter per import
        # run, but `make_export_profile` accepts an instance the caller built
        # -- and `NetexExporter` builds its profile once in `__init__` -- so
        # "one instance per run" is a convention the exporter cannot enforce.
        self.losses: Dict[str, int] = {}

    def begin_run(self) -> None:
        """Forget the previous export's losses.

        Without this, a `NetexExporter` used twice reported the sum of both
        runs: the profile is built in the exporter's `__init__` and the
        counters only ever grew, so the second export of the same feed claimed
        twice the losses it had. The counts are a statement about one document,
        so they start empty for each one.
        """
        self.losses.clear()

    # -- ids and attributes ------------------------------------------------

    def frame_id(self, kind: str, seq: int = 1) -> str:
        return f"{self.id_prefix}:{kind}:{seq:02d}"

    def object_id(self, kind: str, seq) -> str:
        """An id for something this exporter invents (a Route, a Connection)."""
        return f"{self.id_prefix}:{kind}:{seq}"

    def frame_attrs(self, kind: str, seq: int = 1) -> Dict[str, str]:
        attrs = {"id": self.frame_id(kind, seq), "version": self.frame_version}
        if self.modification:
            attrs["modification"] = self.modification
        return attrs

    def entity_attrs(self, entity_id: str, version: Optional[str] = None) -> Dict[str, str]:
        attrs = {"id": entity_id, "version": version or self.entity_version}
        if self.modification:
            attrs["modification"] = self.modification
        return attrs

    def ref_attrs(self, ref: str) -> Dict[str, str]:
        return {"ref": ref, "version": self.entity_version}

    def passing_time_attrs(self) -> Dict[str, str]:
        """Attributes for a TimetabledPassingTime, which carries no id.

        A seam rather than a plain `entity_attrs` call because there is no id to
        pass, and a seam at all because this was the one element in the writer
        that hardcoded `version="0"` -- the profile's `entity_version` never
        reached it, and the value disagreed with every other object in the
        document (auditoria, achado 4).
        """
        attrs = {"version": self.entity_version}
        if self.modification:
            attrs["modification"] = self.modification
        return attrs

    def calendar_entity_attrs(self, entity_id: str) -> Dict[str, str]:
        """Attributes for ServiceCalendar and AvailabilityCondition.

        A seam rather than a plain call to `entity_attrs`, because these are the
        two elements where a published profile and the CEN schema are known to
        disagree about `version` (spec-notas §6a). Inert here: same answer as
        every other entity.
        """
        return self.entity_attrs(entity_id)

    #: Containers that accept `modificationSet`. Not every 1:N container does:
    #: only those whose CEN type derives from containmentAggregationStructure,
    #: and `calls` is not one of them. The set is the profile's own published
    #: examples, and the XSD agrees with it -- writing the attribute on `calls`
    #: is rejected outright, which is how this list stopped being a guess.
    MODIFICATION_SET_CONTAINERS = frozenset({
        "frames", "organisations", "stopPlaces", "routes", "lines",
        "scheduledStopPoints", "contentValidityConditions", "dayTypes",
        "prerequisites", "vehicleJourneys", "codespaces",
    })

    def container_attrs(self, tag: str) -> Dict[str, str]:
        if self.modification_set and tag in self.MODIFICATION_SET_CONTAINERS:
            return {"modificationSet": self.modification_set}
        return {}

    def container(self, parent: ET.Element, tag: str) -> ET.Element:
        """A 1:N container element, carrying `modificationSet` if the profile
        asks for one and the schema admits it there."""
        return ET.SubElement(parent, tag, attrib=self.container_attrs(tag))

    # -- closed enumerations ------------------------------------------------

    def vehicle_mode(self, mode: str) -> str:
        """The VehicleMode to write for an internal transport mode.

        A profile with a closed ``vehicle_modes`` set still gets the *true*
        mode written when the value is outside it, and the divergence is
        counted. Substituting `bus` for `metro` would produce a conformant file
        and a false statement about the network; the file stays CEN-valid
        either way, because the CEN enumeration is the wide one. What the
        caller gets instead is a number they can act on.
        """
        value = netex_mode(mode)
        if self.vehicle_modes is not None and value not in self.vehicle_modes:
            self._count("vehicle_mode")
        return value

    def stop_place_type(self, mode: Optional[str] = None) -> str:
        """The StopPlaceType for a stop served by lines of `mode`.

        Hardcoded to "onstreetBus" until 2026-09-10, so a metro network
        exported every StopPlace as an on-street bus stop while the Line beside
        it correctly said `metro`. That is the mistake `vehicle_mode` exists to
        avoid, on the entity next door: a conformant file making a false
        statement about the network.

        `None` -- a stop no journey pattern calls at -- has no answer in the
        model, and "other" is the enumeration's word for that. It is counted
        like any other value outside a closed profile set, so a PT export says
        how many stops it could not type.
        """
        value = _MODE_TO_STOP_TYPE.get(mode or "", "other")
        if self.stop_place_types is not None and value not in self.stop_place_types:
            self._count("stop_place_type")
        return value

    def _count(self, what: str, n: int = 1, prefix: str = UNSUPPORTED_PREFIX) -> None:
        key = f"{prefix}{what}"
        self.losses[key] = self.losses.get(key, 0) + n

    def note_unsupported(self, what: str, n: int = 1) -> None:
        """Record that `n` things of kind `what` had no place in this profile."""
        if n:
            self._count(what, n)

    def note_unwritten_field(self, entity: str, field: str, n: int = 1) -> None:
        """Record `n` values of `entity.field` this profile's document cannot say."""
        if n:
            self._count(f"{entity}_{field}", n, FIELD_PREFIX)

    def note_dropped(self, what: str, n: int = 1) -> None:
        """Record `n` things the writer had a slot for and could not fill.

        Not the same statement as `note_unsupported`: this profile *can* say the
        thing, and this particular datum still did not come out. The caller who
        sees it should look at the feed, not at the profile.
        """
        if n:
            self._count(what, n, DROPPED_PREFIX)

    def carries(self, entity: str) -> bool:
        """Whether this profile's document says anything at all about `entity`."""
        return entity in self.WRITTEN_FIELDS

    def states(self, entity: str, field: str) -> bool:
        """Whether this profile's document states `entity.field`.

        What a writer asks before placing an optional field. The answer and the
        loss count come from the same table, so a field this returns False for
        is a field the export reports having dropped.
        """
        written = self.WRITTEN_FIELDS.get(entity)
        return written is not None and field in written

    def unwritten_fields(self, entity: str, model_fields: Sequence[str]):
        """The model fields of `entity` this profile's document does not state.

        ``None`` means the writer emits nothing from this entity at all, so the
        caller should count its rows whole instead of column by column.
        """
        written = self.WRITTEN_FIELDS.get(entity)
        if written is None:
            return None
        return [f for f in model_fields if f not in written and f != "extras"]

    # -- frame furniture ----------------------------------------------------

    def write_frame_header(self, frame: ET.Element, kind: str,
                           validity: Optional[Tuple[str, str]] = None) -> None:
        """The frame children every profile shares: ValidBetween, TypeOfFrameRef.

        Order matters and is not the order they are described in: the CEN
        EntityInVersion group puts ``ValidBetween`` before everything an entity
        declares of its own, while ``TypeOfFrameRef`` sits inside the frame's
        own group, after ``Name``/``Description``. The writers append and
        ``_order_frame_children`` sorts, so this only has to produce them.
        """
        if self.write_frame_valid_between and validity:
            start, end = validity
            vb = ET.SubElement(frame, "ValidBetween")
            ET.SubElement(vb, "FromDate").text = f"{start}T00:00:00"
            ET.SubElement(vb, "ToDate").text = f"{end}T23:59:59"
        if self.write_type_of_frame:
            ref = self.TYPE_OF_FRAME.get(kind)
            if ref:
                ET.SubElement(frame, "TypeOfFrameRef",
                              attrib={"ref": ref, "version": self.frame_version})

    def write_types_of_frame(self, frame: ET.Element) -> None:
        """Declare the TypeOfFrame values this document's frames reference.

        ``write_frame_header`` puts a ``TypeOfFrameRef`` on every frame, which
        is what EPIP asks for. The CEN schema also carries an identity
        constraint over them: ``TypeOfFrame_AnyKeyRef`` selects every
        ``.//TypeOfFrameRef`` and refers to a key over ``.//TypeOfFrame``
        ``@id`` + ``@version``. EPIP publishes its five frame types as external
        reference data, so a document that references them without declaring
        them is ordinary EPIP practice *and* invalid against the schema with
        constraints enabled -- one error per reference, with nothing wrong with
        the data. The same document validates with zero errors against
        ``NeTEx_publication-NoConstraint.xsd``, which is how the CEN
        distribution acknowledges the problem.

        Declaring them locally costs one empty element each and makes the
        document self-contained, so it validates either way. A profile that
        writes no ``TypeOfFrameRef`` declares nothing, which is what keeps this
        inert for the Portuguese profile rather than a flag it has to know
        about.
        """
        if not self.write_type_of_frame:
            return
        refs = sorted(set(self.TYPE_OF_FRAME.values()))
        if not refs:
            return
        container = self.container(frame, "typesOfValue")
        for ref in refs:
            ET.SubElement(container, "TypeOfFrame",
                          attrib={"id": ref, "version": self.frame_version})

    def write_composite_frame_defaults(
            self, composite: ET.Element,
            timezone: Optional[str] = None,
            language: Optional[str] = None) -> None:
        """`FrameDefaults`: codespace, locale, coordinate system.

        The locale is the important half. A NeTEx time is a local time and the
        document says nowhere else which local it is, so a file without
        `DefaultLocale/TimeZone` is read as UTC -- an hour of systematic error
        across every passing time in it, silently. `Operator.timezone` is a
        required field of this library's model precisely because a feed cannot
        mean anything without it, and this is where it goes.

        CEN order in `VersionFrameDefaultsStructure`: DefaultCodespaceRef,
        DefaultDataSourceRef, DefaultResponsibilitySetRef, DefaultLocale,
        DefaultLocationSystem, DefaultSystemOfUnits, DefaultCurrency; and inside
        `LocaleStructure`: TimeZoneOffset, TimeZone, ..., DefaultLanguage.
        """
        if not self.write_frame_defaults:
            return
        defaults = ET.SubElement(composite, "FrameDefaults")
        if self.codespace:
            ET.SubElement(defaults, "DefaultCodespaceRef", attrib={"ref": self.codespace})
        if timezone or language:
            locale = ET.SubElement(defaults, "DefaultLocale")
            if timezone:
                ET.SubElement(locale, "TimeZone").text = timezone
            if language:
                ET.SubElement(locale, "DefaultLanguage").text = language
        if self.default_location_system:
            ET.SubElement(defaults, "DefaultLocationSystem").text = self.default_location_system
        if self.default_system_of_units:
            ET.SubElement(defaults, "DefaultSystemOfUnits").text = self.default_system_of_units

    def write_codespaces(self, composite: ET.Element) -> None:
        """Declare the codespace the ids are minted in.

        The PT profile defines ``Codespace``/``Xmlns`` and has a
        ``DefaultCodespaceRef`` pointing at one, but never says where the
        declaration goes; the CEN answer is a ``codespaces`` collection on the
        frame. Writing it is what stops the reference dangling -- the single
        error in the CARRIS file that belongs to no other family.
        """
        if not self.codespace:
            return
        container = self.container(composite, "codespaces")
        # A Codespace is not an EntityInVersion: the CEN schema gives it an id
        # and no `version` attribute at all.
        cs = ET.SubElement(container, "Codespace", attrib={"id": self.codespace})
        ET.SubElement(cs, "Xmlns").text = self.codespace


class EpipProfile(ExportProfile):
    """EPIP, in the shape the EPIP XSD admits.

    The base class is the CEN form of everything this exporter writes, and
    for a long time this subclass overrode nothing: EPIP was taken to be that
    form. Validating against the EPIP XSD published by CEN
    (github.com/NeTEx-CEN/NeTEx-Profile-EPIP, `NeTEx_publication_EPIP.xsd`)
    showed otherwise -- the EPIP XSD narrows the CEN choices in the places the
    flags below name. This class makes those choices; the base keeps the CEN
    ones, so the profiles that inherit it do not change.

    The station structure is not written here. The EPIP StopPlace admits
    quays only -- levels, entrances, access spaces and path links are the
    accessibility profile's (EPIAP) -- so a GTFS station feed exported to EPIP
    reports those records as not carried. `EpipStationsProfile` writes them.
    """

    name = "epip"

    writes_station_structure = False
    writes_stop_point_location = True

    journey_pattern_element = "ServiceJourneyPattern"
    pattern_line_as_route_view = True
    pattern_direction_as_ref = True
    pattern_links_as_onward_refs = True
    journey_writes_line_ref = False
    journey_accessibility_in_key_list = True
    service_link_projection = False
    assignment_writes_stop_place_ref = True
    operator_contact_and_type_required = True
    calendar_in_service_calendar = True

    #: Two narrowings of the base map:
    #:
    #: * the EPIP DataSource is Name, Description and Email -- no ValidBetween
    #:   and no keyList -- so the feed's validity, version, publisher URL and
    #:   contact URL are counted;
    #: * without the station structure, a stop's station, its level and the
    #:   station records themselves have nowhere to go, and are counted (the
    #:   three station entities whole).
    WRITTEN_FIELDS = {
        **{k: v for k, v in ExportProfile.WRITTEN_FIELDS.items()
           if k not in ("stop_area", "level", "pathway")},
        "scheduled_stop_point": ExportProfile.WRITTEN_FIELDS["scheduled_stop_point"]
        - {"stop_area_id", "level_id"},
        "feed_info": frozenset({"id", "publisher_name", "lang",
                                "contact_email"}),
    }


class EpipStationsProfile(EpipProfile):
    """EPIP plus the GTFS station structure.

    Everything `EpipProfile` writes, and each GTFS station as a StopPlace with
    its levels, entrances, quays, access spaces and path links. The extra
    elements are CEN-valid but outside the EPIP XSD, which admits quays only:
    the document validates against the full CEN schema and not against the
    EPIP one. It is the profile to use when the station layout must survive a
    NeTEx round trip.
    """

    name = "epip-stations"
    description = "EPIP plus the station structure (CEN-valid, beyond the EPIP XSD)"

    writes_station_structure = True

    WRITTEN_FIELDS = {
        **ExportProfile.WRITTEN_FIELDS,
        "feed_info": EpipProfile.WRITTEN_FIELDS["feed_info"],
    }


class PortugueseExportProfile(ExportProfile):
    """The IMT/How2Go profile, as published at ptprofiles.azurewebsites.net.

    Read from the spec recovered from the Internet Archive, not from the one
    live file: ``research/docs/perfil-pt/spec-notas.md`` cites every rule below
    to the page it is on, and ``matriz-conformidade.md`` maps them to these
    attributes one by one.

    Four families of difference from EPIP:

    * **The ServiceFrame is a closed list of three collections** -- ``routes``,
      ``lines``, ``scheduledStopPoints``. Journey patterns, stop assignments,
      service links and connections have no place in it, so they are not
      written and are counted as losses.
    * **The organisation is an Authority**, referenced from the Line by
      ``AuthorityRef``; the Line's code is ``PublicCode``, not ``ShortName``.
    * **Stop times are calls**, with Arrival/Departure carrying ForAlighting
      and ForBoarding.
    * **The calendar is per day type**: an AvailabilityCondition names no day
      type, so the only thing that associates one with the other is the
      ServiceCalendarFrame that contains both. One frame per day type is
      therefore not a stylistic choice -- it is the only shape in which the
      exceptions survive being read back.

    Two decisions recorded in the matrix show up here as code:

    * **D6** -- ``ServiceCalendar`` and ``AvailabilityCondition`` get a
      ``version`` even though the published examples omit it, because the CEN
      schema rejects them without one. ``omit_calendar_version`` flips it back
      for a producer who would rather match the examples.
    * **D4** -- the profile's ``VehicleModeEnum`` is {bus, coach}; the true mode
      is written anyway and the divergence counted, rather than a network of
      trams being reported as buses.
    """

    name = "pt"
    description = "Perfil NeTEx português (IMT / How2Go)"

    codespace = "PT"
    id_prefix = "PT"
    frame_version = "1.0"
    entity_version = "any"
    modification = "new"
    modification_set = "all"

    # Restated rather than inherited even where the base now agrees: these are
    # the profile's published requirements, cited by id in the conformance
    # matrix, and a later change to the EPIP baseline must not quietly move
    # them. A subclass that only overrode its disagreements would follow the
    # base into a change PT never made.
    write_frame_defaults = True
    default_location_system = "EPSG:4326"
    default_system_of_units = "SiMetres"
    write_type_of_frame = False
    write_frame_valid_between = False
    write_prerequisites = True

    writes_routes = True
    writes_journey_patterns = False
    writes_quays = False
    writes_station_structure = False
    writes_stop_assignments = False
    writes_service_links = False
    writes_connections = False
    writes_per_day_type_calendars = True
    writes_stop_point_location = True
    writes_journey_direction = True
    writes_journey_pattern_view = True

    organisation_element = "Authority"
    line_operator_ref = "AuthorityRef"
    line_short_name_element = "PublicCode"
    stop_times_element = "calls"

    vehicle_modes = frozenset({"bus", "coach"})
    stop_place_types = frozenset({"onstreetBus", "coachStation"})

    #: Three departures from the baseline, each traceable to a table in
    #: ``spec-notas.md`` §4.2 and §6:
    #:
    #: * the profile's ``Line`` lists Name, Description, TransportMode, Url,
    #:   PublicCode and AuthorityRef -- no ``Presentation``, so the two GTFS
    #:   colours have nowhere to go and are counted;
    #: * its ``ResourceFrame`` admits ``organisations`` and nothing else, so
    #:   there is no ``dataSources`` to put the feed's provenance in and the
    #:   whole ``feed_info`` record is counted (the entity is absent from the
    #:   map, which is how a profile says "nothing of this at all");
    #: * a ``Call`` carries ``ForBoarding``/``ForAlighting``, which a CEN
    #:   ``TimetabledPassingTime`` has no room for -- the one field this
    #:   profile states and EPIP does not.
    WRITTEN_FIELDS = {
        **{k: v for k, v in ExportProfile.WRITTEN_FIELDS.items()
           if k not in ("feed_info", "stop_area", "level", "pathway")},
        # No Quay and only `stopPlaces` in the SiteFrame: the station structure
        # has nowhere to go, and the entities above are counted whole.
        "scheduled_stop_point": ExportProfile.WRITTEN_FIELDS["scheduled_stop_point"]
        - {"stop_area_id", "level_id", "platform_code"},
        "line": ExportProfile.WRITTEN_FIELDS["line"] - {"color", "text_color"},
        "passing_time": (ExportProfile.WRITTEN_FIELDS["passing_time"]
                         | {"pickup_type", "drop_off_type"}),
    }

    def __init__(self, omit_calendar_version: bool = False) -> None:
        super().__init__()
        self.omit_calendar_version = omit_calendar_version

    def calendar_entity_attrs(self, entity_id: str) -> Dict[str, str]:
        """Attributes for ServiceCalendar and AvailabilityCondition.

        The two elements where the published profile and the CEN schema
        disagree (spec-notas §6a). Default is CEN.
        """
        attrs = {"id": entity_id}
        if self.modification:
            attrs["modification"] = self.modification
        if not self.omit_calendar_version:
            attrs["version"] = self.entity_version
        return attrs


_PROFILES = {
    "epip": EpipProfile,
    "epip-stations": EpipStationsProfile,
    "pt": PortugueseExportProfile,
    "portuguese": PortugueseExportProfile,
}


def make_export_profile(profile: "ExportProfile | str | None") -> ExportProfile:
    """Resolve a profile name to a fresh instance; they carry per-run counters.

    Accepts an instance unchanged, so a caller who wants
    ``PortugueseExportProfile(omit_calendar_version=True)`` can build it and
    hand it over. ``None`` and the historical default ``"EPIP"`` both mean EPIP.
    """
    if isinstance(profile, ExportProfile):
        return profile
    if profile is None:
        return EpipProfile()
    key = str(profile).strip().lower()
    try:
        return _PROFILES[key]()
    except KeyError:
        valid = ", ".join(sorted({cls.name for cls in _PROFILES.values()}))
        raise ValueError(
            f"Unknown NeTEx export profile {profile!r}; expected one of: {valid}"
        ) from None
