"""Tests for the NeTEx export profile mechanism (roadmap task 9, phase 4a).

Two properties carry the weight here.

The first is **inertness**: introducing a profile object into a released
exporter must not change a byte of what it already emitted. `EpipProfile`
overrides nothing, so the assertion is that the default output still has the
same frames, the same ids and the same attributes it had before profiles
existed -- which is what makes the parameter safe to add at all.

The second is **that a restriction restricts**: the PT profile is asserted
against the requirements in ``research/docs/perfil-pt/matriz-conformidade.md``,
by requirement id, so a change to the writer that quietly drops one of them
fails here rather than at the NAP.
"""

import xml.etree.ElementTree as ET

import pytest

from pid_transit.core.database import TransmodelDatabase
from pid_transit.adapters.netex_exporter import NetexExporter
from pid_transit.adapters.netex_export_profiles import (
    EpipProfile,
    ExportProfile,
    PortugueseExportProfile,
    make_export_profile,
)
from pid_transit.adapters.netex_importer import NetexImporter
from pid_transit.adapters.netex_profiles import NetexProfile

NS = {"n": "http://www.netex.org.uk/netex"}


def _seeded_db() -> TransmodelDatabase:
    """A network with one of everything the profiles disagree about."""
    db = TransmodelDatabase(":memory:")
    db.upsert("operator", [{"id": "OP1", "name": "Agency",
                            "timezone": "Europe/Lisbon", "lang": "pt",
                            "phone": "555", "url": "https://example.com"}])
    db.upsert("feed_info", [{"id": "FEED", "publisher_name": "Publisher",
                             "publisher_url": "https://publisher.example",
                             "lang": "pt", "start_date": "20260101",
                             "end_date": "20261231", "version": "v7",
                             "contact_email": "data@publisher.example",
                             "contact_url": "https://publisher.example/contact"}])
    db.upsert("line", [
        {"id": "L1", "operator_id": "OP1", "name": "Line One", "short_name": "1",
         "transport_mode": "bus", "description": "The one line", "url": "https://l1",
         # The six fields of achado 1 are seeded here rather than in one test,
         # so every assertion in this module runs against a feed that has them.
         "color": "00AC00", "text_color": "FFFFFF", "sort_order": 3},
        # A mode outside the PT profile's closed VehicleModeEnum.
        {"id": "L2", "operator_id": "OP1", "name": "Line Two", "short_name": "2",
         "transport_mode": "tram"},
    ])
    db.upsert("day_type", [
        {"id": "WK", "monday": True, "start_date": "20260101", "end_date": "20261231"},
        {"id": "SA", "saturday": True, "start_date": "20260101", "end_date": "20261231"},
    ])
    db.upsert("operating_day_exception", [
        {"day_type_id": "WK", "date": "20260425", "is_addition": False},
        {"day_type_id": "SA", "date": "20261225", "is_addition": True},
    ])
    db.upsert("scheduled_stop_point", [
        {"id": "S1", "name": "Stop One", "lat": 38.7, "lon": -9.1, "code": "A1",
         "wheelchair_boarding": 1},
        {"id": "S2", "name": "Stop Two", "lat": 38.8, "lon": -9.2},
    ])
    db.upsert("journey_pattern", [{"id": "JP1", "line_id": "L1",
                                   "direction": "outbound"}])
    db.upsert("point_in_journey_pattern", [
        {"journey_pattern_id": "JP1", "stop_point_id": "S1", "order": 0},
        {"journey_pattern_id": "JP1", "stop_point_id": "S2", "order": 1},
    ])
    db.upsert("service_journey", [
        {"id": "T1", "line_id": "L1", "journey_pattern_id": "JP1",
         "day_type_id": "WK", "departure_time": "08:00:00",
         "wheelchair_accessible": 1},
        # Past midnight, so DayOffset has to survive the calls form.
        {"id": "T2", "line_id": "L1", "journey_pattern_id": "JP1",
         "day_type_id": "SA", "departure_time": "23:50:00"},
    ])
    db.upsert("passing_time", [
        {"service_journey_id": "T1", "stop_point_id": "S1", "order": 0,
         "arrival_time": "08:00:00", "departure_time": "08:00:00"},
        {"service_journey_id": "T1", "stop_point_id": "S2", "order": 1,
         "arrival_time": "08:10:00", "departure_time": "08:10:00",
         "pickup_type": 1},
        {"service_journey_id": "T2", "stop_point_id": "S1", "order": 0,
         "arrival_time": "23:50:00", "departure_time": "23:50:00"},
        {"service_journey_id": "T2", "stop_point_id": "S2", "order": 1,
         "arrival_time": "24:20:00", "departure_time": "24:20:00"},
    ])
    db.upsert("transfer", [{"from_stop_id": "S1", "to_stop_id": "S2",
                            "transfer_type": 2, "min_transfer_time": 300}])
    return db


def _export(db, tmp_path, profile=None, name="feed.xml"):
    out = tmp_path / name
    exporter = NetexExporter(profile=profile or "epip", deduplicate_patterns=False)
    losses = exporter.export_from_db(db, out)
    return ET.parse(out).getroot(), losses


def _tags(parent):
    return [child.tag.rpartition("}")[2] for child in parent]


def _find(root, path):
    return root.find(path, NS)


def _findall(root, path):
    return root.findall(path, NS)


class TestProfileResolution:
    def test_names_and_aliases(self):
        assert isinstance(make_export_profile(None), EpipProfile)
        assert isinstance(make_export_profile("EPIP"), EpipProfile)
        assert isinstance(make_export_profile("pt"), PortugueseExportProfile)
        assert isinstance(make_export_profile("Portuguese"), PortugueseExportProfile)

    def test_an_instance_passes_through(self):
        # So a caller can configure one -- omit_calendar_version, say -- and
        # hand it over rather than having the exporter build it.
        configured = PortugueseExportProfile(omit_calendar_version=True)
        assert make_export_profile(configured) is configured

    def test_an_unknown_profile_names_the_valid_ones(self):
        with pytest.raises(ValueError, match="epip"):
            make_export_profile("bavarian")

    def test_each_export_gets_its_own_counters(self):
        # Losses are per run. Two exports sharing a profile *class* must not
        # share its tally.
        first, second = make_export_profile("pt"), make_export_profile("pt")
        first.note_unsupported("thing", 3)
        assert second.losses == {}


class TestEpipBaseline:
    """What the default document says, and what it deliberately still does not.

    This class used to assert *inertness*: that introducing the profile object
    changed no byte of the released output, which is what made the parameter
    safe to add. That property did its job and is spent -- the 2026-09-10 audit
    found the defaults it froze were the wrong ones (NETEX_EXPORT_AUDITORIA.md,
    achados 1 and 2): EPIP is precisely the profile that requires a codespace
    and frame defaults, and the file was shipping without either. What the class
    asserts now is the baseline that replaced it, item for item, plus the two
    pieces of furniture that are still nobody's default.
    """

    def test_epip_declares_its_codespace_and_frame_defaults(self, tmp_path):
        root, _ = _export(_seeded_db(), tmp_path)
        composite = _find(root, "n:dataObjects/n:CompositeFrame")
        assert _tags(composite) == ["ValidBetween", "TypeOfFrameRef",
                                    "codespaces", "FrameDefaults", "frames"]
        assert composite.attrib == {"id": "PID:CompositeFrame:01", "version": "1"}
        codespace = _find(root, ".//n:codespaces/n:Codespace")
        assert codespace.attrib == {"id": "PID"}
        assert _find(root, ".//n:FrameDefaults/n:DefaultCodespaceRef").attrib["ref"] == "PID"

    def test_the_document_states_its_timezone(self, tmp_path):
        # Achado 1. Every time in a NeTEx file is local, and this is the only
        # place the file says which local: without it the whole timetable is
        # read an hour out, and nothing anywhere reports a problem.
        root, _ = _export(_seeded_db(), tmp_path)
        locale = _find(root, ".//n:FrameDefaults/n:DefaultLocale")
        assert _find(locale, "n:TimeZone").text == "Europe/Lisbon"
        assert _find(locale, "n:DefaultLanguage").text == "pt"

    def test_every_frame_declares_the_profile_it_conforms_to(self, tmp_path):
        # This test used to assert the opposite -- that TypeOfFrameRef stayed
        # off for EPIP because it was "the PT profile's answer". It is not:
        # every value in `TYPE_OF_FRAME` is an `epip:` one, the PT profile
        # switches the element off, and EPIP is the profile that declares
        # itself through exactly this element. Off on both sides, the file
        # named EPIP was the one document that never said so.
        root, _ = _export(_seeded_db(), tmp_path)
        refs = [el.attrib["ref"] for el in _findall(root, ".//n:TypeOfFrameRef")]
        assert refs == [
            "epip:EU_PI_COMMON",    # CompositeFrame
            "epip:EU_PI_COMMON",    # ResourceFrame
            "epip:EU_PI_STOP",
            "epip:EU_PI_NETWORK",
            "epip:EU_PI_CALENDAR",
            "epip:EU_PI_TIMETABLE",
        ]

    def test_every_type_of_frame_it_references_is_declared_in_the_document(
            self, tmp_path):
        """The CEN identity constraint, checked here instead of by a 7-minute XSD run.

        `TypeOfFrame_AnyKeyRef` selects every `.//TypeOfFrameRef` and refers to
        a key over `.//TypeOfFrame` `@id` + `@version`. EPIP publishes its five
        frame types as external reference data, so writing the references
        without the declarations is ordinary EPIP practice and still six schema
        errors -- which is exactly what the Porto export produced once
        TypeOfFrameRef was turned on: valid against
        `NeTEx_publication-NoConstraint.xsd`, six errors against
        `NeTEx_publication.xsd`, and nothing wrong with the data.

        Asserting the pairing rather than the literal set is deliberate: adding
        a sixth frame type to `TYPE_OF_FRAME` must not be able to reintroduce
        the defect.
        """
        root, _ = _export(_seeded_db(), tmp_path)
        referenced = {(el.attrib["ref"], el.attrib.get("version"))
                      for el in _findall(root, ".//n:TypeOfFrameRef")}
        declared = {(el.attrib["id"], el.attrib.get("version"))
                    for el in _findall(root, ".//n:TypeOfFrame")}
        assert referenced, "the baseline must reference its profile"
        assert referenced <= declared, (
            f"referenced but not declared: {sorted(referenced - declared)}")
        # Nothing declared that nothing points at, either.
        assert declared == referenced

    def test_the_declarations_sit_where_the_resource_frame_group_puts_them(
            self, tmp_path):
        # CEN ResourceFrameGroup: DataManagementInFrameGroup (dataSources) ->
        # TypeOfValueInFrameGroup (typesOfValue) -> OrganisationInFrameGroup.
        # Out of order it is a schema error, and the writer appends rather than
        # sorting here, so the order is the call order.
        root, _ = _export(_seeded_db(), tmp_path)
        frame = _find(root, "n:dataObjects/n:CompositeFrame/n:frames/n:ResourceFrame")
        tags = _tags(frame)
        assert "typesOfValue" in tags
        if "dataSources" in tags:
            assert tags.index("dataSources") < tags.index("typesOfValue")
        assert tags.index("typesOfValue") < tags.index("organisations")

    def test_every_frame_states_the_window_it_is_valid_over(self, tmp_path):
        # Derived from the day types rather than invented; the exporter was
        # already computing it and dropping it on the floor.
        root, _ = _export(_seeded_db(), tmp_path)
        frame = _find(root, "n:dataObjects/n:CompositeFrame")
        assert _find(frame, "n:ValidBetween/n:FromDate").text == "2026-01-01T00:00:00"
        assert _find(frame, "n:ValidBetween/n:ToDate").text == "2026-12-31T23:59:59"

    def test_what_is_still_nobody_s_default(self, tmp_path):
        # modificationSet remains off for EPIP: it is the PT profile's answer,
        # and the base must not acquire it by drift.
        root, _ = _export(_seeded_db(), tmp_path)
        composite = _find(root, "n:dataObjects/n:CompositeFrame")
        assert "modificationSet" not in _find(composite, "n:frames").attrib

    def test_epip_still_writes_the_frames_it_always_did(self, tmp_path):
        root, _ = _export(_seeded_db(), tmp_path)
        frames = _find(root, "n:dataObjects/n:CompositeFrame/n:frames")
        assert _tags(frames) == [
            "ResourceFrame", "SiteFrame", "ServiceFrame",
            "ServiceCalendarFrame", "TimetableFrame",
        ]

    def test_epip_loses_only_what_it_reports_losing(self, tmp_path):
        # Not `== {}` any more, and that is the correction rather than a
        # regression: EPIP has no slot for a GTFS route_sort_order and none for
        # per-stop-time boarding permissions, and it used to say so nowhere.
        # The EPIP DataSource is Name, Description and Email, so the feed's
        # validity, version and URLs are counted too.
        _, losses = _export(_seeded_db(), tmp_path)
        assert losses == {
            "unsupported_field_line_sort_order": 1,
            "unsupported_field_passing_time_pickup_type": 1,
            "unsupported_field_feed_info_contact_url": 1,
            "unsupported_field_feed_info_end_date": 1,
            "unsupported_field_feed_info_publisher_url": 1,
            "unsupported_field_feed_info_start_date": 1,
            "unsupported_field_feed_info_version": 1,
        }

    def test_epip_differs_from_the_base_only_where_its_xsd_does(self):
        # EpipProfile was empty until the EPIP XSD showed that it narrows the
        # CEN forms the base writes. Every difference is one of those
        # narrowings; anything else here would be an EPIP decision leaking
        # into what the other profiles inherit, or the reverse.
        base, epip = ExportProfile(), EpipProfile()
        differing = {
            attr for attr in vars(ExportProfile)
            if not attr.startswith("_")
            and not callable(getattr(base, attr, None))
            and getattr(epip, attr) != getattr(base, attr)
        }
        assert differing == {
            "writes_station_structure", "writes_stop_point_location",
            "journey_pattern_element", "pattern_line_as_route_view",
            "pattern_direction_as_ref", "pattern_links_as_onward_refs",
            "journey_writes_line_ref", "journey_accessibility_in_key_list",
            "service_link_projection", "assignment_writes_stop_place_ref",
            "operator_contact_and_type_required", "calendar_in_service_calendar",
            "WRITTEN_FIELDS",
        }


class TestPortugueseProfileStructure:
    """Requirement ids are those of ``matriz-conformidade.md``."""

    @pytest.fixture
    def exported(self, tmp_path):
        return _export(_seeded_db(), tmp_path, profile="pt", name="pt.xml")

    def test_e4_e5_e6_e7_frame_defaults(self, exported):
        root, _ = exported
        defaults = _find(root, "n:dataObjects/n:CompositeFrame/n:FrameDefaults")
        assert _find(defaults, "n:DefaultCodespaceRef").attrib["ref"] == "PT"
        assert _find(defaults, "n:DefaultLocationSystem").text == "EPSG:4326"
        assert _find(defaults, "n:DefaultSystemOfUnits").text == "SiMetres"

    def test_e8_codespace_is_declared_not_only_referenced(self, exported):
        # The dangling DefaultCodespaceRef is the one CARRIS schema error that
        # belongs to no other family; declaring the Codespace is what closes it.
        root, _ = exported
        codespace = _find(root, ".//n:codespaces/n:Codespace")
        assert codespace.attrib == {"id": "PT"}       # no `version`: not an EntityInVersion
        assert _find(codespace, "n:Xmlns").text == "PT"

    def test_e9_modification_set_only_where_the_schema_admits_it(self, exported):
        root, _ = exported
        assert _find(root, ".//n:lines").attrib["modificationSet"] == "all"
        # `calls` is not a containment aggregation, and the XSD rejects the
        # attribute there outright.
        assert "modificationSet" not in _find(root, ".//n:calls").attrib

    def test_e12_timetable_declares_its_prerequisite(self, exported):
        root, _ = exported
        ref = _find(root, ".//n:TimetableFrame/n:prerequisites/n:ServiceFrameRef")
        assert ref.attrib["ref"] == "PT:ServiceFrame:01"

    def test_n1_n3_n4_routes_carry_the_pattern_identity(self, exported):
        # The profile has no JourneyPattern anywhere, so a Route is minted per
        # pattern and keeps its id -- the only handle JourneyPatternView has.
        root, _ = exported
        route = _find(root, ".//n:routes/n:Route")
        assert route.attrib["id"] == "JP1"
        assert _find(route, "n:LineRef").attrib["ref"] == "L1"
        assert _find(route, "n:DirectionType").text == "outbound"

    def test_r2_n11_organisation_is_an_authority(self, exported):
        root, _ = exported
        assert _tags(_find(root, ".//n:organisations")) == ["Authority"]
        assert _find(root, ".//n:Line/n:AuthorityRef").attrib["ref"] == "OP1"
        assert not _findall(root, ".//n:OperatorRef")

    def test_n10_line_children_are_in_cen_order(self, exported):
        # PublicCode sits *after* TransportMode and Url in the CEN Line
        # sequence, where ShortName sits before both. Choosing the element
        # chooses the slot, and the schema rejects the other arrangement.
        root, _ = exported
        line = _findall(root, ".//n:lines/n:Line")[0]
        assert _tags(line) == ["Name", "Description", "TransportMode", "Url",
                               "PublicCode", "AuthorityRef"]

    def test_n14_n15_n16_stop_point_carries_its_own_location(self, exported):
        root, _ = exported
        ssp = _findall(root, ".//n:scheduledStopPoints/n:ScheduledStopPoint")[0]
        assert _tags(ssp) == ["Name", "Location", "PrivateCode", "StopType"]
        assert _find(ssp, "n:StopType").text == "onstreetBus"

    def test_n17_to_n20_the_service_frame_is_the_profile_s_three_collections(self, exported):
        root, _ = exported
        frame = _find(root, ".//n:ServiceFrame")
        assert _tags(frame) == ["routes", "lines", "scheduledStopPoints"]

    def test_s7_no_quays(self, exported):
        root, _ = exported
        assert not _findall(root, ".//n:Quay")

    def test_c1_c2_c3_c7_c9_one_calendar_frame_per_day_type(self, exported):
        # An AvailabilityCondition names no day type: containment in one frame
        # is the only thing that associates them, which is why the frame is per
        # day type and not per feed.
        root, _ = exported
        frames = _findall(root, ".//n:ServiceCalendarFrame")
        assert len(frames) == 2
        assert _tags(frames[0]) == ["contentValidityConditions",
                                    "ServiceCalendar", "dayTypes"]
        condition = _find(frames[0], "n:contentValidityConditions"
                                     "/n:AvailabilityCondition")
        assert _find(condition, "n:FromDate").text.startswith("2026-04-25")
        assert _find(condition, "n:ToDate").text.startswith("2026-04-25")
        assert _find(condition, "n:IsAvailable").text == "false"
        calendar = _find(frames[0], "n:ServiceCalendar")
        assert _find(calendar, "n:FromDate").text == "2026-01-01"

    def test_c8_no_day_type_assignments(self, exported):
        root, _ = exported
        assert not _findall(root, ".//n:DayTypeAssignment")

    def test_t2_the_journey_names_its_day_type(self, exported):
        # The element the CARRIS file omits in all 10 238 of its journeys,
        # forcing a positional guess on whoever reads it. We hold the key.
        root, _ = exported
        journey = _findall(root, ".//n:vehicleJourneys/n:ServiceJourney")[0]
        assert _find(journey, "n:dayTypes/n:DayTypeRef").attrib["ref"] in ("WK", "SA")

    def test_t3_t4_t5_t6_journey_children_are_in_profile_order(self, exported):
        root, _ = exported
        journey = _findall(root, ".//n:vehicleJourneys/n:ServiceJourney")[0]
        assert _tags(journey) == ["dayTypes", "LineRef", "DirectionType",
                                  "JourneyPatternView", "calls"]
        assert _find(journey, "n:JourneyPatternView/n:RouteRef").attrib["ref"] == "JP1"

    def test_t7_t8_t9_t10_calls_carry_boarding_permission(self, exported):
        # ForAlighting/ForBoarding are 1:1 in the profile and carry real data:
        # a stop where GTFS forbids pickup must not be written as boardable.
        root, _ = exported
        calls = _findall(root, ".//n:vehicleJourneys/n:ServiceJourney"
                               "[@id='T1']/n:calls/n:Call")
        assert [c.attrib["order"] for c in calls] == ["1", "2"]
        assert _tags(calls[0]) == ["ScheduledStopPointRef", "Arrival", "Departure"]
        assert _find(calls[0], "n:Departure/n:ForBoarding").text == "true"
        assert _find(calls[1], "n:Departure/n:ForBoarding").text == "false"
        assert _find(calls[1], "n:Arrival/n:ForAlighting").text == "true"

    def test_t11_no_timetabled_passing_times(self, exported):
        root, _ = exported
        assert not _findall(root, ".//n:TimetabledPassingTime")

    def test_t12_day_offset_survives_service_past_midnight(self, exported):
        # The profile's tables do not list DayOffset, but without it a 24:20
        # arrival is unrepresentable -- and the IMT's own file uses it in all
        # 296 603 of its calls.
        root, _ = exported
        call = _findall(root, ".//n:ServiceJourney[@id='T2']/n:calls/n:Call")[1]
        arrival = _find(call, "n:Arrival")
        assert _find(arrival, "n:Time").text == "00:20:00"
        assert _find(arrival, "n:DayOffset").text == "1"
        assert _tags(arrival) == ["Time", "DayOffset", "ForAlighting"]


class TestPortugueseProfileDeclaresNoFrameTypes:
    def test_a_profile_that_references_none_declares_none(self, tmp_path):
        """The inertness the declaration mechanism has to keep.

        The PT profile switches `write_type_of_frame` off, so it writes no
        `TypeOfFrameRef` -- and must therefore write no `typesOfValue` either.
        A declaration nothing points at would be the base profile's answer
        leaking into a profile that did not ask for it, which is the one thing
        the subclass arrangement exists to prevent.
        """
        root, _ = _export(_seeded_db(), tmp_path, profile="pt", name="pt.xml")
        assert _findall(root, ".//n:TypeOfFrameRef") == []
        assert _findall(root, ".//n:TypeOfFrame") == []
        assert _findall(root, ".//n:typesOfValue") == []


class TestPortugueseProfileLosses:
    """What a restriction cannot carry is a number, not a silence."""

    @pytest.fixture
    def losses(self, tmp_path):
        return _export(_seeded_db(), tmp_path, profile="pt", name="pt.xml")[1]

    def test_patterns_and_their_points_are_counted(self, losses):
        assert losses["unsupported_journey_pattern"] == 1
        assert losses["unsupported_point_in_journey_pattern"] == 2

    def test_transfers_are_counted(self, losses):
        assert losses["unsupported_transfer"] == 1

    def test_a_mode_outside_the_closed_enum_is_counted_not_substituted(
            self, tmp_path, losses):
        # Decision D4: writing `bus` for a tram would make the file conformant
        # and the statement false. The true mode is written and the divergence
        # reported.
        assert losses["unsupported_vehicle_mode"] == 1
        root, _ = _export(_seeded_db(), tmp_path, profile="pt", name="modes.xml")
        modes = [m.text for m in _findall(root, ".//n:Line/n:TransportMode")]
        assert "tram" in modes

    def test_a_field_the_profile_has_no_slot_for_is_counted(self, losses):
        # The PT Line is a closed list (spec-notas §4.2) with no `Presentation`,
        # so the two GTFS colours have nowhere to go. EPIP places them; the
        # difference between the profiles is now a number rather than a silence.
        assert losses["unsupported_field_line_color"] == 1
        assert losses["unsupported_field_line_text_color"] == 1

    def test_an_entity_the_profile_has_no_frame_for_is_counted_whole(self, losses):
        # The PT ResourceFrame admits `organisations` and nothing else, so there
        # is no dataSources to put the feed's provenance in.
        assert losses["unsupported_feed_info"] == 1

    def test_the_calls_form_carries_what_a_passing_time_cannot(self, losses):
        # The one field this profile states and EPIP does not: a Call has
        # ForBoarding/ForAlighting, a CEN TimetabledPassingTime has neither.
        assert "unsupported_field_passing_time_pickup_type" not in losses
        assert "unsupported_field_passing_time_drop_off_type" not in losses

    def test_nothing_is_counted_for_what_the_profile_can_say(self, losses):
        for key in ("unsupported_line", "unsupported_service_journey",
                    "unsupported_passing_time", "unsupported_day_type",
                    "unsupported_stop_place_type"):
            assert key not in losses


class TestPortugueseRoundTrip:
    """Export PT, read it back, and check what survived — and what did not.

    The acceptance criterion for phase 4 is a round trip, and the honest form
    of it names both halves: the entities the profile can carry come back
    exactly, and the ones it cannot are the ones the export said it was
    dropping. A test that asserted only the first half would be passing off a
    known loss as a success.
    """

    @pytest.fixture
    def reimported(self, tmp_path):
        source = _seeded_db()
        out = tmp_path / "pt.xml"
        losses = NetexExporter(profile="pt",
                               deduplicate_patterns=False).export_from_db(source, out)
        target = TransmodelDatabase(":memory:")
        importer = NetexImporter()
        stats = importer.import_to_db(target, out)
        return source, target, importer, stats, losses

    def test_the_dialect_is_recognised_as_its_own(self, reimported):
        _, _, importer, _, _ = reimported
        assert importer.last_profile is NetexProfile.PORTUGUESE

    def test_everything_the_profile_carries_comes_back(self, reimported):
        source, target, _, _, _ = reimported
        for table in ("operator", "line", "scheduled_stop_point", "day_type",
                      "operating_day_exception", "service_journey",
                      "passing_time"):
            assert target.count(table) == source.count(table), table

    def test_the_calendar_exceptions_land_on_the_right_day_types(self, reimported):
        # The part that only works because the frames are per day type: with a
        # single frame the conditions have no owner and come back as day types
        # that no journey runs on.
        _, target, _, _, _ = reimported
        got = {(e["day_type_id"], e["date"], bool(e["is_addition"]))
               for e in target.get_records("operating_day_exception")}
        assert got == {("WK", "20260425", False), ("SA", "20261225", True)}

    def test_journeys_keep_their_own_calendar_not_a_positional_guess(self, reimported):
        _, target, _, _, _ = reimported
        by_id = {j["id"]: j["day_type_id"]
                 for j in target.get_records("service_journey")}
        assert by_id == {"T1": "WK", "T2": "SA"}

    def test_reading_back_reports_no_loss_of_its_own(self, reimported):
        # The export's losses are the export's. The import must not add any:
        # anything the file does contain has to be readable.
        _, _, _, stats, _ = reimported
        assert not [k for k in stats if k.startswith(("skipped_", "dropped_"))]

    def test_what_is_missing_is_exactly_what_the_export_reported(self, reimported):
        _, target, _, _, losses = reimported
        assert target.count("journey_pattern") == 0
        assert target.count("transfer") == 0
        assert losses["unsupported_journey_pattern"] == 1
        assert losses["unsupported_transfer"] == 1


class TestPatternlessPortugueseRoundTrip:
    """The second lap: a PT feed re-exported after it has already lost its
    patterns.

    This is not a corner case, it is the normal case, and it was a real defect
    until the full-scale run of phase 6 exposed it. The PT profile declares no
    JourneyPattern, so importing a PT feed yields none; exporting that database
    back to PT therefore writes no ``JourneyPatternView`` at all, and detection
    keyed on that element could not see the dialect. The file fell through to
    the Swiss rule — which matched, because it asked only whether
    AvailabilityConditions existed — and its 18 081 exception dates came back
    as day types that no journey runs on.
    """

    @pytest.fixture
    def second_lap(self, tmp_path):
        source = _seeded_db()
        # A database in the state a PT import leaves: journeys, calendars and
        # exceptions, but no patterns to hang a pattern view on.
        source.upsert("service_journey", [
            {"id": "T1", "line_id": "L1", "journey_pattern_id": None,
             "day_type_id": "WK", "departure_time": "08:00:00"},
            {"id": "T2", "line_id": "L1", "journey_pattern_id": None,
             "day_type_id": "SA", "departure_time": "23:50:00"},
        ])
        source.delete("point_in_journey_pattern", {"journey_pattern_id": "JP1"})
        source.delete("journey_pattern", {"id": "JP1"})

        out = tmp_path / "second.xml"
        NetexExporter(profile="pt",
                      deduplicate_patterns=False).export_from_db(source, out)
        target = TransmodelDatabase(":memory:")
        importer = NetexImporter()
        importer.import_to_db(target, out)
        return source, target, importer, out

    def test_no_pattern_view_is_written_when_there_is_no_pattern(self, second_lap):
        _, _, _, out = second_lap
        root = ET.parse(out).getroot()
        assert not _findall(root, ".//n:JourneyPatternView")

    def test_the_dialect_is_still_recognised(self, second_lap):
        _, _, importer, _ = second_lap
        assert importer.last_profile is NetexProfile.PORTUGUESE

    def test_exceptions_stay_exceptions(self, second_lap):
        # The failure mode this guards: read as Swiss, each AvailabilityCondition
        # becomes its own day type and the exception table empties.
        source, target, _, _ = second_lap
        assert target.count("day_type") == source.count("day_type")
        assert (target.count("operating_day_exception")
                == source.count("operating_day_exception"))


class TestWrittenFieldsIsOneDeclaration:
    """`WRITTEN_FIELDS` is both the gate and the ledger, or it is neither.

    The point of the table is that a profile cannot state a field in one place
    and report losing it in another. That only holds if the writers actually
    ask: until 2026-09-10 five fields consulted `states()` and the rest were
    written unconditionally, so removing a field from the table changed the
    loss count and not the document. Each case below removes one field and
    asserts the element goes with it.
    """

    CASES = [
        ("operator", "phone", "n:organisations/n:Operator/n:ContactDetails/n:Phone"),
        ("operator", "url", "n:organisations/n:Operator/n:ContactDetails/n:Url"),
        ("feed_info", "contact_email", "n:dataSources/n:DataSource/n:Email"),
        ("scheduled_stop_point", "wheelchair_boarding",
         "n:stopPlaces/n:StopPlace/n:AccessibilityAssessment"),
        ("scheduled_stop_point", "code", "n:stopPlaces/n:StopPlace/n:PrivateCode"),
        ("line", "short_name", "n:lines/n:Line/n:ShortName"),
        ("line", "description", "n:lines/n:Line/n:Description"),
        ("line", "url", "n:lines/n:Line/n:Url"),
        ("journey_pattern", "direction",
         "n:journeyPatterns/n:ServiceJourneyPattern/n:DirectionRef"),
        ("transfer", "min_transfer_time",
         "n:connections/n:Connection/n:WalkTransferDuration"),
        ("service_journey", "wheelchair_accessible",
         "n:vehicleJourneys/n:ServiceJourney/n:keyList"),
    ]

    @pytest.mark.parametrize("entity,field,path", CASES)
    def test_a_field_the_table_drops_leaves_the_document(
            self, tmp_path, entity, field, path):
        db = _seeded_db()
        present, _ = _export(db, tmp_path, name="with.xml")
        assert _findall(present, f".//{path}"), (
            f"the fixture must exercise {entity}.{field} for this to mean anything"
        )

        class Narrowed(EpipProfile):
            WRITTEN_FIELDS = {
                **EpipProfile.WRITTEN_FIELDS,
                entity: EpipProfile.WRITTEN_FIELDS[entity] - {field},
            }

        out = tmp_path / "without.xml"
        losses = NetexExporter(profile=Narrowed(),
                               deduplicate_patterns=False).export_from_db(db, out)
        absent = ET.parse(out).getroot()
        assert not _findall(absent, f".//{path}")
        assert losses[f"unsupported_field_{entity}_{field}"] >= 1
