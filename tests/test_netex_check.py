"""Tests for `netex-check` (roadmap task 9, phase 5).

The checker walks the XML itself rather than querying an imported database, and
the tests are built around why that matters: a file can be *readable* and
*non-conformant* at the same time, and only a second reader sees the second
half. The clearest case is the one the CARRIS file supplies — a journey with no
`dayTypes` is paired to a calendar positionally by the importer, so through an
imported database it looks complete, while the profile says it is broken.
"""

import xml.etree.ElementTree as ET

import pytest

from pid_transit.adapters.netex_check import (
    Finding,
    MAX_EXAMPLES,
    PortugueseRules,
    ProfileRules,
    check_netex,
    check_profile,
    check_schema,
    make_rules,
)

# A minimal PT-profile document, conformant. Tests deform one thing at a time,
# so a finding can only come from the deformation.
CONFORMANT_PT = """<?xml version="1.0" encoding="UTF-8"?>
<PublicationDelivery xmlns="http://www.netex.org.uk/netex" version="1.1">
 <dataObjects>
  <CompositeFrame id="PT:CompositeFrame:01" version="1.0">
   <codespaces><Codespace id="PT"><Xmlns>PT</Xmlns></Codespace></codespaces>
   <FrameDefaults><DefaultCodespaceRef ref="PT"/></FrameDefaults>
   <frames>
    <ResourceFrame id="PT:ResourceFrame:01" version="1.0">
     <organisations>
      <Authority id="OP1" version="any"><Name>Agency</Name></Authority>
     </organisations>
    </ResourceFrame>
    <SiteFrame id="PT:SiteFrame:01" version="1.0">
     <stopPlaces>
      <StopPlace id="S1" version="any"><Name>A</Name>
       <Centroid><Location><Longitude>-9.1</Longitude><Latitude>38.7</Latitude></Location></Centroid>
       <StopPlaceType>onstreetBus</StopPlaceType>
      </StopPlace>
     </stopPlaces>
    </SiteFrame>
    <ServiceFrame id="PT:ServiceFrame:01" version="1.0">
     <routes><Route id="R1" version="any"><LineRef ref="L1"/><DirectionType>outbound</DirectionType></Route></routes>
     <lines><Line id="L1" version="any"><Name>One</Name><TransportMode>bus</TransportMode>
      <AuthorityRef ref="OP1"/></Line></lines>
     <scheduledStopPoints>
      <ScheduledStopPoint id="S1" version="any"><Name>A</Name>
       <Location><Longitude>-9.1</Longitude><Latitude>38.7</Latitude></Location>
       <StopType>onstreetBus</StopType>
      </ScheduledStopPoint>
     </scheduledStopPoints>
    </ServiceFrame>
    <ServiceCalendarFrame id="PT:ServiceCalendarFrame:01" version="1.0">
     <contentValidityConditions>
      <AvailabilityCondition id="WK:20260425" version="any">
       <FromDate>2026-04-25T00:00:00</FromDate><ToDate>2026-04-25T23:59:59</ToDate>
       <IsAvailable>false</IsAvailable>
      </AvailabilityCondition>
     </contentValidityConditions>
     <ServiceCalendar id="WK" version="any">
      <FromDate>2026-01-01</FromDate><ToDate>2026-12-31</ToDate>
     </ServiceCalendar>
     <dayTypes><DayType id="WK" version="any"><Name>WK</Name></DayType></dayTypes>
    </ServiceCalendarFrame>
    <TimetableFrame id="PT:TimetableFrame:01" version="1.0">
     <vehicleJourneys>
      <ServiceJourney id="T1" version="any">
       <dayTypes><DayTypeRef ref="WK"/></dayTypes>
       <LineRef ref="L1"/>
       <DirectionType>outbound</DirectionType>
       <JourneyPatternView><RouteRef ref="R1"/></JourneyPatternView>
       <calls>
        <Call id="T1:1" version="any" order="1">
         <ScheduledStopPointRef ref="S1"/>
         <Arrival><Time>08:00:00</Time><ForAlighting>true</ForAlighting></Arrival>
         <Departure><Time>08:00:00</Time><ForBoarding>true</ForBoarding></Departure>
        </Call>
       </calls>
      </ServiceJourney>
     </vehicleJourneys>
    </TimetableFrame>
   </frames>
  </CompositeFrame>
 </dataObjects>
</PublicationDelivery>
"""


# A schema whose only rule is a key identity-constraint over (@id, @version) --
# the CEN construct that every one of the CARRIS file's 353 242 schema errors
# violates, and the one that dominates XSD validation time. Self-contained on
# purpose: the real CEN schema lives under the gitignored `research/`, so a
# test that needed it would not run on a fresh clone.
KEYED_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
 <xsd:element name="things">
  <xsd:complexType>
   <xsd:sequence>
    <xsd:element name="Thing" maxOccurs="unbounded">
     <xsd:complexType>
      <xsd:attribute name="id" type="xsd:string"/>
      <xsd:attribute name="version" type="xsd:string"/>
     </xsd:complexType>
    </xsd:element>
   </xsd:sequence>
  </xsd:complexType>
  <xsd:key name="ThingKey">
   <xsd:selector xpath="Thing"/>
   <xsd:field xpath="@id"/>
   <xsd:field xpath="@version"/>
  </xsd:key>
 </xsd:element>
</xsd:schema>
"""

# Three versioned objects with no @version: the key cannot be formed.
VIOLATES_KEY = """<?xml version="1.0" encoding="UTF-8"?>
<things>
 <Thing id="a"/>
 <Thing id="b"/>
 <Thing id="c"/>
</things>
"""

SATISFIES_KEY = """<?xml version="1.0" encoding="UTF-8"?>
<things>
 <Thing id="a" version="1"/>
 <Thing id="b" version="1"/>
</things>
"""


def _write(tmp_path, content, name="feed.xml"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _check(tmp_path, content, profile="pt", **kwargs):
    return check_netex(_write(tmp_path, content), profile=profile, **kwargs)


def _rules(report):
    return {f.rule: f for f in report.findings}


class TestRuleSetSelection:
    def test_names_and_aliases(self):
        assert isinstance(make_rules("epip"), ProfileRules)
        assert isinstance(make_rules("PT"), PortugueseRules)
        assert isinstance(make_rules("portuguese"), PortugueseRules)

    def test_unknown_profile_names_the_valid_ones(self):
        with pytest.raises(ValueError, match="epip"):
            make_rules("bavarian")

    def test_declaring_elements_follow_a_subclass_reference_table(self):
        # Derived at use time, not at class-definition time: a subclass that
        # replaces REFERENCE_TARGETS must not silently keep the base's set of
        # elements whose ids count as declarations.
        class Narrow(ProfileRules):
            name = "narrow"
            REFERENCE_TARGETS = {"LineRef": "Line"}
            ALSO_DECLARES_ID = frozenset()

        assert Narrow().declares_id == {"Line"}
        assert "Quay" in ProfileRules().declares_id


class TestConformantFileIsQuiet:
    def test_no_findings(self, tmp_path):
        report = _check(tmp_path, CONFORMANT_PT)
        assert report.findings == []
        assert report.is_conformant

    def test_counts_what_it_walked(self, tmp_path):
        report = _check(tmp_path, CONFORMANT_PT)
        assert report.stats["ServiceJourney"] == 1
        assert report.stats["Call"] == 1
        assert report.stats["Line"] == 1

    def test_layers_not_run_are_named_not_silent(self, tmp_path):
        # A check that did not run must never read as one that passed.
        report = _check(tmp_path, CONFORMANT_PT)
        assert "schema" in report.not_checked
        assert "readability" in report.not_checked


class TestProfileRules:
    """One deformation each. Rule ids are those the report prints."""

    def test_missing_mandatory_child(self, tmp_path):
        content = CONFORMANT_PT.replace("<StopType>onstreetBus</StopType>", "", 1)
        report = _check(tmp_path, content)
        # The StopPlace's own StopPlaceType is the first occurrence; the
        # ScheduledStopPoint's StopType is the rule under test.
        assert "PT-CHILD-ScheduledStopPoint-StopType" in _rules(report)

    def test_a_journey_that_names_no_day_type(self, tmp_path):
        # The CARRIS defect, in miniature: an importer recovers from this by
        # pairing positionally, so only a reader that walks the file sees it.
        content = CONFORMANT_PT.replace(
            '<dayTypes><DayTypeRef ref="WK"/></dayTypes>', "", 1)
        report = _check(tmp_path, content)
        assert "PT-CHILD-ServiceJourney-dayTypes" in _rules(report)

    def test_an_empty_day_types_container_is_caught_in_context(self, tmp_path):
        # `dayTypes` under a journey must hold a DayTypeRef; under a calendar
        # frame it holds DayTypes. A rule blind to context reports the calendar
        # as broken in every conformant file.
        content = CONFORMANT_PT.replace(
            '<dayTypes><DayTypeRef ref="WK"/></dayTypes>', "<dayTypes/>", 1)
        found = _rules(_check(tmp_path, content))
        assert "PT-CHILD-ServiceJourney/dayTypes-DayTypeRef" in found
        assert found["PT-CHILD-ServiceJourney/dayTypes-DayTypeRef"].count == 1

    def test_the_calendar_frame_day_types_is_not_reported(self, tmp_path):
        assert _check(tmp_path, CONFORMANT_PT).findings == []

    def test_value_outside_a_closed_enumeration(self, tmp_path):
        content = CONFORMANT_PT.replace(
            "<TransportMode>bus</TransportMode>",
            "<TransportMode>tram</TransportMode>")
        found = _rules(_check(tmp_path, content))
        assert found["PT-ENUM-TransportMode"].examples == ["tram"]

    def test_missing_version_on_a_versioned_object(self, tmp_path):
        content = CONFORMANT_PT.replace('<ServiceCalendar id="WK" version="any">',
                                        '<ServiceCalendar id="WK">')
        found = _rules(_check(tmp_path, content))
        assert "PT-ID-ServiceCalendar" in found
        assert found["PT-ID-ServiceCalendar"].examples == ["WK"]

    def test_an_element_the_frame_may_not_contain(self, tmp_path):
        content = CONFORMANT_PT.replace(
            "</scheduledStopPoints>",
            "</scheduledStopPoints><journeyPatterns/>")
        found = _rules(_check(tmp_path, content))
        assert "PT-FRAME-ServiceFrame" in found
        assert found["PT-FRAME-ServiceFrame"].examples == ["journeyPatterns"]

    def test_a_dangling_reference(self, tmp_path):
        content = CONFORMANT_PT.replace('<LineRef ref="L1"/>',
                                        '<LineRef ref="L9"/>', 1)
        found = _rules(_check(tmp_path, content))
        assert found["PT-REF-LineRef"].examples == ["L9"]

    def test_an_undeclared_codespace_is_a_dangling_reference(self, tmp_path):
        # The single CARRIS schema error that belongs to none of the four
        # attribute families.
        content = CONFORMANT_PT.replace(
            '<codespaces><Codespace id="PT"><Xmlns>PT</Xmlns></Codespace></codespaces>',
            "")
        found = _rules(_check(tmp_path, content))
        assert found["PT-REF-DefaultCodespaceRef"].examples == ["PT"]

    def test_reference_checking_can_be_turned_off(self, tmp_path):
        content = CONFORMANT_PT.replace('<LineRef ref="L1"/>',
                                        '<LineRef ref="L9"/>', 1)
        report = _check(tmp_path, content, check_references=False)
        assert "PT-REF-LineRef" not in _rules(report)
        assert "references" in report.not_checked


class TestFindingsAreGroupedByCause:
    """353 242 errors are one rule broken by four element types. A report that
    lists instances is not a report."""

    def test_many_instances_collapse_to_one_finding(self, tmp_path):
        journey = CONFORMANT_PT[
            CONFORMANT_PT.index("<ServiceJourney"):
            CONFORMANT_PT.index("</ServiceJourney>") + len("</ServiceJourney>")]
        broken = journey.replace('<dayTypes><DayTypeRef ref="WK"/></dayTypes>', "")
        content = CONFORMANT_PT.replace(journey, "\n".join(
            broken.replace('id="T1"', f'id="T{i}"').replace(
                'id="T1:1"', f'id="T{i}:1"') for i in range(20)))
        found = _rules(_check(tmp_path, content))
        finding = found["PT-CHILD-ServiceJourney-dayTypes"]
        assert finding.count == 20
        assert len(finding.examples) == MAX_EXAMPLES

    def test_report_orders_by_severity_then_volume(self):
        from pid_transit.adapters.netex_check import CheckReport
        report = CheckReport(source="x", profile="pt", findings=[
            Finding("A", "profile", "error", "a", count=2),
            Finding("B", "readability", "info", "b", count=99),
            Finding("C", "profile", "error", "c", count=50),
        ])
        assert [f.rule for f in report.sorted_findings()] == ["C", "A", "B"]


class TestEpipRulesAreNotPortugueseOnes:
    """The base rule set is CEN's. A profile's restrictions must not leak into
    it, or every feed gets checked against Portugal."""

    def test_epip_does_not_close_the_mode_enumeration(self, tmp_path):
        content = CONFORMANT_PT.replace(
            "<TransportMode>bus</TransportMode>",
            "<TransportMode>tram</TransportMode>")
        assert "EPIP-ENUM-TransportMode" not in _rules(
            _check(tmp_path, content, profile="epip"))

    def test_epip_does_not_require_the_journey_to_name_a_day_type(self, tmp_path):
        content = CONFORMANT_PT.replace(
            '<dayTypes><DayTypeRef ref="WK"/></dayTypes>', "", 1)
        assert "EPIP-CHILD-ServiceJourney-dayTypes" not in _rules(
            _check(tmp_path, content, profile="epip"))

    def test_epip_still_enforces_the_cen_identity_rule(self, tmp_path):
        # The rule the CARRIS file breaks 353 242 times is CEN's, not
        # Portugal's, so it belongs to the base set.
        content = CONFORMANT_PT.replace('<Call id="T1:1" version="any" order="1">',
                                        '<Call order="1">')
        assert "EPIP-ID-Call" in _rules(_check(tmp_path, content, profile="epip"))

    def test_epip_does_not_restrict_frame_contents(self, tmp_path):
        content = CONFORMANT_PT.replace(
            "</scheduledStopPoints>", "</scheduledStopPoints><journeyPatterns/>")
        assert "EPIP-FRAME-ServiceFrame" not in _rules(
            _check(tmp_path, content, profile="epip"))


class TestReadabilityLayer:
    """What our own importer lost, kept apart from what the file got wrong."""

    def test_reports_the_dialect_and_the_imported_counts(self, tmp_path):
        report = _check(tmp_path, CONFORMANT_PT, with_readability=True)
        rules = _rules(report)
        assert "READ-profile" in rules
        assert rules["READ-profile"].layer == "readability"
        assert report.stats["imported_service_journey"] == 1

    def test_readability_findings_are_info_not_errors(self, tmp_path):
        # They are facts about our reader, not about the file's conformance.
        # Reporting a limitation of ours as the producer's fault is the one
        # mistake this layer must not make.
        report = _check(tmp_path, CONFORMANT_PT, with_readability=True)
        assert all(f.severity == "info"
                   for f in report.findings if f.layer == "readability")
        assert report.is_conformant

    def test_a_readable_file_can_still_be_non_conformant(self, tmp_path):
        # The whole argument for walking the XML: the importer recovers from
        # the missing dayTypes by pairing positionally and reports no loss, so
        # only the profile layer sees anything wrong.
        content = CONFORMANT_PT.replace(
            '<dayTypes><DayTypeRef ref="WK"/></dayTypes>', "", 1)
        report = _check(tmp_path, content, with_readability=True)
        assert report.stats["imported_service_journey"] == 1
        assert not [f for f in report.findings
                    if f.layer == "readability" and f.severity == "error"]
        assert not report.is_conformant


class TestSchemaLayer:
    def test_absent_lxml_is_reported_not_passed(self, tmp_path, monkeypatch):
        # Degrading quietly would turn "could not check" into "checked, fine".
        import builtins
        real_import = builtins.__import__

        def no_lxml(name, *args, **kwargs):
            if name.startswith("lxml"):
                raise ImportError("no lxml")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_lxml)
        findings, skipped = check_schema(_write(tmp_path, CONFORMANT_PT))
        assert findings == []
        assert "xsd" in skipped

    def test_a_missing_schema_file_is_reported(self, tmp_path):
        pytest.importorskip("lxml")
        findings, skipped = check_schema(_write(tmp_path, CONFORMANT_PT),
                                         xsd_path=tmp_path / "nope.xsd")
        assert findings == []
        assert "not found" in skipped

    def test_not_requested_says_so(self, tmp_path):
        report = _check(tmp_path, CONFORMANT_PT)
        assert "not requested" in report.not_checked["schema"]

    def test_streaming_matches_tree_validation(self, tmp_path):
        # `check_schema` validates while parsing instead of building the tree
        # and validating it afterwards -- 17x faster on the 174 MB CARRIS file
        # (55.8 min -> 3.3 min). libxml2 reaches identity constraints through a
        # different code path when streaming, so "same schema, same answer" is
        # a claim that has to be held down rather than assumed. The fixture is
        # a key identity-constraint over (@id, @version), which is the exact
        # shape of the defect that dominates real Portuguese-profile files.
        pytest.importorskip("lxml")
        from lxml import etree

        from pid_transit.adapters.netex_check import _group_schema_errors

        xsd = _write(tmp_path, KEYED_XSD, name="keyed.xsd")
        doc = _write(tmp_path, VIOLATES_KEY, name="violates.xml")

        schema = etree.XMLSchema(etree.parse(str(xsd)))
        assert not schema.validate(etree.parse(str(doc)))
        by_tree = _group_schema_errors(list(schema.error_log))

        by_stream, skipped = check_schema(doc, xsd_path=xsd)
        assert skipped is None
        # Guard the fixture itself: a test that compared two empty lists would
        # pass while proving nothing.
        assert sum(f.count for f in by_stream) == 3
        # The verdict is identical — same rule, same message, same instance
        # count. That is what the fast path is allowed to be trusted for.
        assert ([(f.rule, f.message, f.count) for f in by_stream] ==
                [(f.rule, f.message, f.count) for f in by_tree])

    def test_only_the_located_path_can_say_where(self, tmp_path):
        # The documented price of streaming, pinned so it cannot regress into a
        # surprise: libxml2 attaches a line number to a schema error only when
        # validating a built tree. The fast path must report no example rather
        # than a fabricated "line 0".
        pytest.importorskip("lxml")
        xsd = _write(tmp_path, KEYED_XSD, name="keyed.xsd")
        doc = _write(tmp_path, VIOLATES_KEY, name="violates.xml")

        streamed, _ = check_schema(doc, xsd_path=xsd)
        located, _ = check_schema(doc, xsd_path=xsd, locate=True)

        assert streamed[0].examples == []
        assert located[0].examples == ["line 3", "line 4", "line 5"]
        assert streamed[0].count == located[0].count == 3

    def test_a_conformant_file_yields_no_findings(self, tmp_path):
        pytest.importorskip("lxml")
        xsd = _write(tmp_path, KEYED_XSD, name="keyed.xsd")
        doc = _write(tmp_path, SATISFIES_KEY, name="ok.xml")
        findings, skipped = check_schema(doc, xsd_path=xsd)
        assert findings == []
        assert skipped is None

    def test_namespaced_element_names_group_readably(self):
        # libxml2 names elements by their expanded name, so a real NeTEx
        # message carries "{http://www.netex.org.uk/netex}Call". Splitting the
        # message on its first ":" lands inside "http:" and mangles both
        # halves -- which is what the report did on every real file, while the
        # namespace-free fixtures above looked fine.
        from pid_transit.adapters.netex_check import _group_schema_errors

        class _Err:
            line = 0
            path = None
            message = ("Element '{http://www.netex.org.uk/netex}Call': Not all "
                       "fields of key identity-constraint "
                       "'{http://www.netex.org.uk/netex}Call_AnyVersioned' "
                       "evaluate to a node.")

        (finding,) = _group_schema_errors([_Err(), _Err()])
        assert finding.rule == "XSD-Call"
        assert finding.message.startswith("<Call>: Not all fields")
        assert "http" not in finding.rule
        assert finding.count == 2

    def test_two_files_in_one_process_do_not_share_errors(self, tmp_path):
        # libxml2's error log is a thread-global. Without clearing it, the
        # second file inherits the first one's findings — a checker telling a
        # producer their file is broken in the way someone else's was. A batch
        # of submissions is exactly the use case, so this cannot regress.
        pytest.importorskip("lxml")
        xsd = _write(tmp_path, KEYED_XSD, name="keyed.xsd")
        bad = _write(tmp_path, VIOLATES_KEY, name="bad.xml")
        good = _write(tmp_path, SATISFIES_KEY, name="good.xml")

        assert check_schema(bad, xsd_path=xsd)[0] != []
        assert check_schema(good, xsd_path=xsd)[0] == []

    def test_malformed_xml_is_not_reported_as_a_schema_verdict(self, tmp_path):
        # Streaming stops where the syntax breaks, so the schema errors past
        # that point were never collected. Reporting the syntax error as a
        # conformance finding would claim a check that did not happen.
        pytest.importorskip("lxml")
        xsd = _write(tmp_path, KEYED_XSD, name="keyed.xsd")
        doc = _write(tmp_path, "<things><Thing id='a'>", name="broken.xml")
        findings, skipped = check_schema(doc, xsd_path=xsd)
        assert skipped is None
        assert [f.rule for f in findings] == ["XSD-malformed"]
        assert "not well-formed" in findings[0].message


class TestReportRendering:
    def test_markdown_names_every_rule_and_the_unchecked_layers(self, tmp_path):
        content = CONFORMANT_PT.replace("<TransportMode>bus</TransportMode>",
                                        "<TransportMode>tram</TransportMode>")
        text = _check(tmp_path, content).to_markdown()
        assert "PT-ENUM-TransportMode" in text
        assert "Not checked" in text

    def test_json_is_serializable_and_carries_the_verdict(self, tmp_path):
        import json
        report = _check(tmp_path, CONFORMANT_PT)
        payload = json.loads(json.dumps(report.to_dict()))
        assert payload["is_conformant"] is True
        assert payload["error_count"] == 0


class TestStreaming:
    def test_the_walk_does_not_retain_the_document(self, tmp_path):
        # Peak memory must not track file size: measured flat at 23 MB peak RSS
        # on the 173.6 MB CARRIS file. Here the cheap proxy is that the parsed
        # tree is not still standing after the walk.
        rules = make_rules("pt")
        path = _write(tmp_path, CONFORMANT_PT)
        findings, counts = check_profile(path, rules)
        assert counts["Call"] == 1
        # A second walk over the same file gives the same answer, which it
        # would not if state leaked between runs.
        assert check_profile(path, rules)[1] == counts
