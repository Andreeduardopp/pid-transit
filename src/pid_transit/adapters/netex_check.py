"""Check a NeTEx file against a profile, without importing it.

Three claims of different strength, kept apart on purpose (roadmap task 9,
phase 5). Running them together and reporting one verdict is the quickest way
to lose a technical argument, so the report labels every finding with the layer
it came from:

``schema``
    The file is valid against the CEN XSD. Needs ``lxml`` (the ``xsd`` extra);
    when it is absent the check says so rather than passing quietly. Still by
    far the expensive layer -- 3.3 and 8.6 minutes in two runs over the same
    174 MB file, against 6-12 s for ``profile``. Essentially all of it is key
    identity-constraints: *every one* of that file's 353 242 errors is a
    ``SCHEMAV_CVC_IDC``, and stripping the 2 428 ``xsd:key``/``keyref``/
    ``unique`` from the schema drops validation of a subset from ~73 s to
    ~0.2 s -- and to zero findings, which is why that is a diagnosis and not an
    optimisation. Opt-in, not a default.
``profile``
    The file obeys the national profile's cardinalities, closed enumerations
    and frame contents. Cheap, streaming, and the layer with the most to say to
    a regulator.
``readability``
    What *this library's importer* could not read out of the file. Reported as
    a finding rather than as an absence, which is the whole reason the profile
    layer walks the XML itself instead of querying an imported database: an
    importer that recovers gracefully from a defect would otherwise hide it.

Why a second reader
-------------------
The obvious implementation is to import the file and reuse the V01-V20 semantic
validator. It is cheaper, and it is wrong for this use. A submission checker
must not inherit the blind spots of its own reader: the CARRIS file omits the
``DayTypeRef`` its profile makes mandatory, and our importer *recovers* by
pairing journeys to calendars positionally. Through an imported database that
file looks complete. The cost of the second reader is that it can drift from
the importer, which is why ``readability`` runs the importer over the same file
and reports what it lost, keeping the two answers side by side.

Memory
------
The walk is ``iterparse`` with pruning, so element state is O(depth). Reference
checking is the exception: it holds the set of declared ids and the set of
referenced ids, which is O(distinct ids) -- about 90 MB on the 173 MB CARRIS
file. ``check_references=False`` turns it off for a caller who cannot pay that.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

#: How many offending ids a finding carries. A report that lists 296 603
#: instances is unreadable; the count plus a handful of examples is what a
#: producer needs to find the bug.
MAX_EXAMPLES = 5

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

#: libxml2 schema errors read "Element 'name': the rest". The name is the
#: expanded one -- "{http://www.netex.org.uk/netex}Call" -- so the split has to
#: be on the quoted name, never on the first colon.
_SCHEMA_MESSAGE = re.compile(r"^Element '(?P<element>[^']*)':\s*(?P<rest>.*)$",
                             re.S)


@dataclass
class Finding:
    """One rule, violated N times -- never one finding per instance.

    Grouping by cause is a requirement rather than a presentation choice: the
    CARRIS file's 353 242 schema errors are *one* rule broken by four element
    types, and a report that says so is actionable where a list of 353 242
    lines is not.
    """

    rule: str
    layer: str
    severity: str
    message: str
    count: int = 1
    examples: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule, "layer": self.layer, "severity": self.severity,
            "message": self.message, "count": self.count,
            "examples": self.examples,
        }


@dataclass
class CheckReport:
    source: str
    profile: str
    findings: List[Finding] = field(default_factory=list)
    #: Layers that did not run, and why. A check that could not check
    #: something says so; silence would read as a pass.
    not_checked: Dict[str, str] = field(default_factory=dict)
    stats: Dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def is_conformant(self) -> bool:
        return not self.errors

    def sorted_findings(self) -> List[Finding]:
        return sorted(self.findings,
                      key=lambda f: (SEVERITY_ORDER.get(f.severity, 9),
                                     -f.count, f.rule))

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "profile": self.profile,
            "is_conformant": self.is_conformant,
            "error_count": len(self.errors),
            "finding_count": len(self.findings),
            "not_checked": self.not_checked,
            "stats": self.stats,
            "findings": [f.to_dict() for f in self.sorted_findings()],
        }

    def to_markdown(self) -> str:
        lines = [f"# NeTEx check — {self.source}", "",
                 f"Profile: **{self.profile}**", ""]
        if self.stats:
            lines.append("| Element | Count |")
            lines.append("|---|---:|")
            for name, count in sorted(self.stats.items()):
                lines.append(f"| {name} | {count:,} |")
            lines.append("")
        if not self.findings:
            lines.append("No findings.")
        else:
            lines.append("| Rule | Layer | Severity | Instances | Finding |")
            lines.append("|---|---|---|---:|---|")
            for f in self.sorted_findings():
                lines.append(f"| {f.rule} | {f.layer} | {f.severity} | "
                             f"{f.count:,} | {f.message} |")
            lines.append("")
            for f in self.sorted_findings():
                if f.examples:
                    lines.append(f"- **{f.rule}** e.g. " +
                                 ", ".join(f"`{e}`" for e in f.examples))
        if self.not_checked:
            lines.append("")
            lines.append("## Not checked")
            for layer, why in sorted(self.not_checked.items()):
                lines.append(f"- **{layer}**: {why}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rule sets
# ---------------------------------------------------------------------------

class ProfileRules:
    """What a profile requires, as tables rather than as code.

    A new profile is a subclass that replaces the tables -- never an ``if
    profile == ...`` inside the walker. The walker below knows how to apply a
    cardinality, a closed enumeration and a reference; it knows nothing about
    any particular profile, which is what stops a new one changing how the
    others are checked.

    The base is the CEN/EPIP baseline: the rule that the CARRIS file breaks
    353 242 times (a versioned object needs ``id`` *and* ``version``, or the
    schema's key identity-constraint fails) belongs here, because it is CEN's,
    not Portugal's.
    """

    name = "epip"

    #: Elements whose absence of these children is a violation, by parent.
    REQUIRED_CHILDREN: Dict[str, Tuple[str, ...]] = {
        "Location": ("Longitude", "Latitude"),
    }
    #: The same, but keyed on (parent, element). NeTEx reuses container names
    #: for unrelated things -- a `dayTypes` under a ServiceJourney holds
    #: DayTypeRefs, one under a ServiceCalendarFrame holds DayTypes -- so a
    #: rule that ignored context would report the calendar's container as
    #: broken every time. Context is the parent's name, which the walk has.
    REQUIRED_CHILDREN_IN: Dict[Tuple[str, str], Tuple[str, ...]] = {}
    #: Attributes a versioned object must carry. The CEN identity constraint
    #: needs both; a profile may relax it.
    VERSIONED_ATTRS: Tuple[str, ...] = ("id", "version")
    #: Elements the identity constraint applies to. Deliberately a list of the
    #: element types real feeds get wrong, not every element in NeTEx: a rule
    #: nobody breaks costs a scan and says nothing.
    VERSIONED_ELEMENTS: FrozenSet[str] = frozenset({
        "Line", "Route", "ScheduledStopPoint", "StopPlace", "Quay", "DayType",
        "ServiceCalendar", "AvailabilityCondition", "ServiceJourney",
        "TemplateServiceJourney", "Call", "JourneyPattern",
        "ServiceJourneyPattern", "Operator", "Authority", "ServiceLink",
    })
    #: Element -> the set of values it may take.
    CLOSED_ENUMS: Dict[str, FrozenSet[str]] = {
        "DirectionType": frozenset({"outbound", "inbound", "clockwise",
                                    "anticlockwise"}),
    }
    #: Container -> the only children a profile admits in it.
    ALLOWED_CHILDREN: Dict[str, FrozenSet[str]] = {}
    #: Reference element -> the element type it must resolve to.
    REFERENCE_TARGETS: Dict[str, str] = {
        "LineRef": "Line",
        "RouteRef": "Route",
        "ScheduledStopPointRef": "ScheduledStopPoint",
        "DayTypeRef": "DayType",
        "OperatorRef": "Operator",
        "AuthorityRef": "Authority",
        "QuayRef": "Quay",
        "JourneyPatternRef": "JourneyPattern",
        # The one CARRIS schema error that belongs to none of the four
        # attribute families: a DefaultCodespaceRef naming a Codespace the
        # document never declares.
        "DefaultCodespaceRef": "Codespace",
    }
    #: Extra elements whose `id` declares a name others may reference, beyond
    #: the targets named above.
    ALSO_DECLARES_ID: FrozenSet[str] = frozenset({
        "ServiceJourneyPattern", "TemplateServiceJourney", "ServiceJourney",
    })

    @property
    def declares_id(self) -> FrozenSet[str]:
        """Derived from `REFERENCE_TARGETS` at *use* time, not class-definition
        time, so a subclass that replaces the targets does not silently keep
        the base's set of declaring elements."""
        return frozenset(self.REFERENCE_TARGETS.values()) | self.ALSO_DECLARES_ID
    #: Elements counted for the report's summary table.
    COUNTED: FrozenSet[str] = frozenset({
        "Line", "Route", "ScheduledStopPoint", "StopPlace", "DayType",
        "ServiceJourney", "TemplateServiceJourney", "Call",
        "TimetabledPassingTime", "AvailabilityCondition", "JourneyPattern",
    })
    #: Rule ids and their prose, so the report cites a requirement rather than
    #: paraphrasing one.
    RULE_TEXT: Dict[str, str] = {}

    def rule_id(self, kind: str, subject: str) -> str:
        return f"{self.name.upper()}-{kind}-{subject}"


class PortugueseRules(ProfileRules):
    """The IMT/How2Go profile.

    Every table below is derived from ``research/docs/perfil-pt/spec-notas.md``
    and cited by requirement id in ``matriz-conformidade.md``: each 1:1 or 1:*
    cardinality became a `REQUIRED_CHILDREN` entry and each closed enumeration
    a `CLOSED_ENUMS` one. Nothing here is invention -- if a rule cannot be
    pointed at a line of the recovered spec, it does not belong.

    Two deliberate departures from the published examples, both recorded as
    decisions in the matrix:

    * ``ServiceCalendar`` and ``AvailabilityCondition`` are still required to
      carry ``version`` (D6), because the CEN schema rejects them without it.
      The profile's own examples omit it; a producer who follows them will get
      this finding, and that is the argument, not a bug.
    * ``DayOffset`` on a call is accepted although the profile's tables do not
      list it (spec-notas §6d): without it, service past midnight cannot be
      expressed at all.
    """

    name = "pt"

    REQUIRED_CHILDREN = {
        "Location": ("Longitude", "Latitude"),          # framework, 1:1
        "Line": ("Name",),                              # N6, 1:1
        "ScheduledStopPoint": ("StopType",),            # N16, 1:1
        "AvailabilityCondition": ("IsAvailable",),      # C2, 1:1
        "Arrival": ("Time", "ForAlighting"),            # T9, 1:1 both
        "Departure": ("Time", "ForBoarding"),           # T10, 1:1 both
        "ServiceJourney": ("dayTypes",),                # T2, VehicleJourney 1:*
        "Codespace": ("Xmlns",),                        # framework, 1:1
    }

    REQUIRED_CHILDREN_IN = {
        # The journey's own dayTypes, not the calendar frame's.
        ("ServiceJourney", "dayTypes"): ("DayTypeRef",),
    }

    CLOSED_ENUMS = {
        "TransportMode": frozenset({"bus", "coach"}),
        "StopPlaceType": frozenset({"onstreetBus", "coachStation"}),
        "StopType": frozenset({"onstreetBus", "coachStation"}),
        "DirectionType": frozenset({"outbound", "inbound", "clockwise",
                                    "anticlockwise"}),
    }

    ALLOWED_CHILDREN = {
        # framework.html, "Frame Elements": the profile lists exactly these.
        "ServiceFrame": frozenset({"routes", "lines", "scheduledStopPoints"}),
        "ResourceFrame": frozenset({"organisations"}),
        "SiteFrame": frozenset({"stopPlaces"}),
        "ServiceCalendarFrame": frozenset({"contentValidityConditions",
                                           "ServiceCalendar", "dayTypes"}),
        "TimetableFrame": frozenset({"vehicleJourneys", "prerequisites"}),
    }

    RULE_TEXT = {
        "PT-FRAME-ServiceFrame":
            "the profile's ServiceFrame admits only routes, lines and "
            "scheduledStopPoints",
        "PT-CHILD-ServiceJourney-dayTypes":
            "VehicleJourney/dayTypes is 1:* — the journey must name its DayType",
    }


_RULE_SETS = {
    "epip": ProfileRules,
    "pt": PortugueseRules,
    "portuguese": PortugueseRules,
}


def make_rules(profile: str) -> ProfileRules:
    try:
        return _RULE_SETS[str(profile).strip().lower()]()
    except KeyError:
        valid = ", ".join(sorted({c.name for c in _RULE_SETS.values()}))
        raise ValueError(
            f"Unknown NeTEx profile {profile!r}; expected one of: {valid}"
        ) from None


# ---------------------------------------------------------------------------
# The profile walk
# ---------------------------------------------------------------------------

@dataclass
class _Open:
    """An element the walk is inside: its name, attributes and children so far.

    Children are recorded as each one *ends*, with the parent read off this
    stack, because by the time the parent ends its children have been pruned
    away — counting them at the parent's end undercounts, silently. Holding
    names only keeps this O(depth).
    """

    name: str
    attrib: dict
    element: ET.Element
    children: set = field(default_factory=set)


class _Tally:
    """Violations of one rule, collapsed to a count and a few examples."""

    def __init__(self) -> None:
        self.count = 0
        self.examples: List[str] = []

    def add(self, example: Optional[str]) -> None:
        self.count += 1
        if example and len(self.examples) < MAX_EXAMPLES:
            self.examples.append(example)


def _localname(tag: str) -> str:
    return tag.rpartition("}")[2]


def check_profile(source: Union[Path, str], rules: ProfileRules,
                  check_references: bool = True) -> Tuple[List[Finding],
                                                          Dict[str, int]]:
    """Stream the file and apply `rules`. Returns (findings, element counts)."""
    tallies: Dict[str, _Tally] = {}
    messages: Dict[str, Tuple[str, str]] = {}   # rule -> (severity, message)
    counts: Dict[str, int] = {}
    declared: set = set()
    referenced: Dict[str, set] = {}
    declares_id = rules.declares_id

    def violation(rule: str, severity: str, message: str,
                  example: Optional[str]) -> None:
        if rule not in tallies:
            tallies[rule] = _Tally()
            messages[rule] = (severity, rules.RULE_TEXT.get(rule, message))
        tallies[rule].add(example)

    stack: List[_Open] = []
    for event, elem in ET.iterparse(str(source), events=("start", "end")):
        name = _localname(elem.tag)
        if event == "start":
            stack.append(_Open(name, elem.attrib, elem))
            continue

        open_el = stack.pop()
        parent = stack[-1] if stack else None
        if parent is not None:
            parent.children.add(name)

        if name in rules.COUNTED:
            counts[name] = counts.get(name, 0) + 1

        entity_id = open_el.attrib.get("id")

        # -- identity: id, and version where the CEN key needs it -----------
        if name in rules.VERSIONED_ELEMENTS:
            missing = [a for a in rules.VERSIONED_ATTRS
                       if a not in open_el.attrib]
            if missing:
                violation(
                    f"{rules.name.upper()}-ID-{name}", "error",
                    f"<{name}> is a versioned object and must carry "
                    f"{' and '.join(rules.VERSIONED_ATTRS)}; "
                    f"missing {', '.join(missing)}",
                    entity_id or f"<{name}>")

        # -- cardinality ----------------------------------------------------
        contextual = rules.REQUIRED_CHILDREN_IN.get(
            (parent.name, name)) if parent is not None else None
        for required in (tuple(rules.REQUIRED_CHILDREN.get(name, ()))
                         + tuple(contextual or ())):
            if required in open_el.children:
                continue
            # A container like `dayTypes` has no id of its own, so name the
            # parent that does -- the journey, not the anonymous container.
            example = entity_id
            if example is None and parent is not None:
                example = parent.attrib.get("id")
            context = f"{parent.name}/" if contextual else ""
            violation(
                f"{rules.name.upper()}-CHILD-{context}{name}-{required}",
                "error",
                f"<{context}{name}> requires <{required}>",
                example)

        # -- closed enumerations --------------------------------------------
        allowed = rules.CLOSED_ENUMS.get(name)
        if allowed is not None:
            value = (elem.text or "").strip()
            if value and value not in allowed:
                violation(
                    f"{rules.name.upper()}-ENUM-{name}", "error",
                    f"<{name}> must be one of "
                    f"{{{', '.join(sorted(allowed))}}}",
                    value)

        # -- frame contents --------------------------------------------------
        permitted = rules.ALLOWED_CHILDREN.get(name)
        if permitted is not None:
            for child in sorted(open_el.children - permitted):
                violation(
                    f"{rules.name.upper()}-FRAME-{name}", "error",
                    f"<{name}> may not contain <{child}> in this profile",
                    child)

        # -- references -----------------------------------------------------
        if check_references:
            if name in declares_id and entity_id:
                declared.add(entity_id)
            target = rules.REFERENCE_TARGETS.get(name)
            if target is not None:
                ref = open_el.attrib.get("ref")
                if ref:
                    referenced.setdefault(name, set()).add(ref)

        # Prune: everything this element had to say has been said, and the
        # just-ended element is the last child of the one on top of the stack,
        # so detaching it is O(1). Without this the whole document accumulates
        # and peak memory tracks file size.
        elem.clear()
        if parent is not None:
            container = parent.element
            if len(container) and container[-1] is elem:
                del container[-1]

    if check_references:
        for ref_name, refs in referenced.items():
            dangling = sorted(refs - declared)
            if dangling:
                target = rules.REFERENCE_TARGETS[ref_name]
                rule = f"{rules.name.upper()}-REF-{ref_name}"
                tallies[rule] = _Tally()
                tallies[rule].count = len(dangling)
                tallies[rule].examples = dangling[:MAX_EXAMPLES]
                messages[rule] = (
                    "error",
                    f"<{ref_name}> points at a {target} the file never declares")

    findings = [
        Finding(rule=rule, layer="profile", severity=messages[rule][0],
                message=messages[rule][1], count=tally.count,
                examples=tally.examples)
        for rule, tally in tallies.items()
    ]
    return findings, counts


# ---------------------------------------------------------------------------
# The other two layers
# ---------------------------------------------------------------------------

DEFAULT_XSD = Path("research/validator/tools/netex_xsd/NeTEx_publication.xsd")


def _group_schema_errors(errors) -> List[Finding]:
    """Collapse an lxml error log into one Finding per broken rule.

    Kept separate from the validation call so a test can feed it the error log
    of *either* validation path and compare what comes out -- which is how the
    streaming switch is held to producing the same report as the tree walk.
    """
    grouped: Dict[Tuple[str, str], _Tally] = {}
    for error in errors:
        # "Element 'X': message" -> group on (X, the message's first clause),
        # which is what collapses one broken rule from N instances to one row.
        # Splitting on the first ":" cannot do this: libxml2 names elements by
        # their expanded name, so the first colon is the one in "http:" and
        # both halves come out mangled.
        match = _SCHEMA_MESSAGE.match(error.message)
        if match:
            element = _localname(match.group("element"))
            shape = match.group("rest").strip()[:120]
        else:
            element, shape = "?", error.message.strip()[:120]
        key = (element, shape)
        # Streaming validation reports every schema error at line 0 with no
        # path. Recording "line 0" five times would be false precision; a
        # finding with no examples at least says it has none.
        where = f"line {error.line}" if error.line else error.path
        grouped.setdefault(key, _Tally()).add(where)

    return [
        Finding(rule=f"XSD-{element}", layer="schema", severity="error",
                message=f"<{element}>: {shape}", count=tally.count,
                examples=tally.examples)
        for (element, shape), tally in grouped.items()
    ]


def check_schema(source: Union[Path, str],
                 xsd_path: Union[Path, str, None] = None,
                 locate: bool = False) -> Tuple[List[Finding], Optional[str]]:
    """Validate against the CEN XSD. Returns (findings, reason it was skipped).

    Requires ``lxml`` — ``pip install pid-transit[xsd]``. Without it this
    returns no findings *and a reason*, which the report prints under "Not
    checked": a validation that did not run must never read as one that passed.

    Errors are grouped by their schema message shape rather than listed. The
    CARRIS file produces 353 242 of them and they are four rules; a report that
    prints them all is not a report.

    Validation streams by default — the schema is handed to the parser instead
    of being run over a built tree, for the same errors grouped into the same
    findings (353 242 either way on the CARRIS file).

    How much faster is measured but **not yet pinned down**: 2.4x and 3.6x on a
    17.9 MB subset in two runs, and 3.3 min / 8.6 min on the full 174 MB file
    against a 55.8 min tree-path baseline recorded on 2026-09-08. The machine
    those came from shows run-to-run variance of over 2x on work that should be
    deterministic (compiling the same schema ranged 14.1-33.8 s), so treat the
    direction as established and the factor as somewhere between 6x and 17x
    until it is measured n>=3 on a quiet machine.

    ``locate=True`` buys back the one thing streaming costs. libxml2 attaches a
    line number and an XPath to a schema error only when it validates a tree;
    streaming reports the same error with ``line 0`` and no path, so findings
    from the fast path carry counts but no examples. Pay the tree path's cost
    when a producer needs to be told *where*, not just *what*.
    """
    try:
        from lxml import etree
    except ImportError:
        return [], ("lxml is not installed; install the 'xsd' extra "
                    "(pip install pid-transit[xsd]) to check against the "
                    "CEN schema")

    path = Path(xsd_path) if xsd_path else DEFAULT_XSD
    if not path.exists():
        return [], f"CEN schema not found at {path}"

    schema = etree.XMLSchema(etree.parse(str(path)))
    # libxml2's error log is a thread-global that the parser's log proxies, so
    # without this a second call reports the first call's errors — a checker
    # that told a producer their file was broken in the way the *previous*
    # file was. Caught by test_two_files_in_one_process_do_not_share_errors.
    etree.clear_error_log()

    if locate:
        doc = etree.parse(str(source))
        if schema.validate(doc):
            return [], None
        return _group_schema_errors(schema.error_log), None

    parser = etree.XMLParser(schema=schema)
    try:
        etree.parse(str(source), parser)
        return [], None
    except etree.XMLSyntaxError as exc:
        errors = list(parser.error_log) or list(exc.error_log)
        # A truncated document records nothing in either log — the message
        # lives only on the exception.
        if not errors:
            return [Finding(
                rule="XSD-malformed", layer="schema", severity="error",
                message=("the file is not well-formed XML, so it was never "
                         f"checked against the schema: {str(exc)[:120]}"),
                count=1, examples=[f"line {exc.lineno}"])], None

    # A document that is not well-formed never reached the schema, and parsing
    # stopped where it broke — so every schema error past that point is
    # *missing*, not absent. Saying "not well-formed" is the honest answer;
    # reporting the syntax error as a conformance finding would claim the file
    # was checked when it was not.
    malformed = [e for e in errors if not e.type_name.startswith("SCHEMAV")]
    if malformed:
        first = malformed[0]
        return [Finding(
            rule="XSD-malformed", layer="schema", severity="error",
            message=("the file is not well-formed XML, so it was never checked "
                     f"against the schema: {first.message[:120]}"),
            count=1, examples=[f"line {first.line}"])], None

    return _group_schema_errors(errors), None


def check_readability(source: Union[Path, str]) -> Tuple[List[Finding],
                                                         Dict[str, int]]:
    """What this library's importer could not read out of the file.

    A finding class of its own, and the reason the profile layer does not run
    on an imported database: an importer that recovers from a defect makes the
    defect invisible downstream. Reported as ``info`` because these are facts
    about *our reader*, not about the file's conformance — conflating the two
    would let a limitation of ours be reported as the producer's fault.
    """
    from ..core.database import TransmodelDatabase
    from .netex_importer import LOSS_PREFIXES, NetexImporter

    db = TransmodelDatabase(":memory:")
    importer = NetexImporter()
    try:
        stats = importer.import_to_db(db, source)
    finally:
        db.close()

    findings = [
        Finding(rule=f"READ-{key}", layer="readability", severity="info",
                message=f"the importer could not read {count:,} entities "
                        f"({key})",
                count=count)
        for key, count in sorted(stats.items())
        if key.startswith(LOSS_PREFIXES)
    ]
    imported = {k: v for k, v in stats.items() if not k.startswith(LOSS_PREFIXES)}
    if importer.last_profile is not None:
        findings.append(Finding(
            rule="READ-profile", layer="readability", severity="info",
            message=f"the importer read this file as the "
                    f"'{importer.last_profile.value}' dialect", count=1))
    return findings, imported


def check_netex(source: Union[Path, str], profile: str = "epip",
                with_schema: bool = False, with_readability: bool = False,
                xsd_path: Union[Path, str, None] = None,
                xsd_locate: bool = False,
                check_references: bool = True) -> CheckReport:
    """Run the layers asked for and return one report.

    Only the profile layer runs by default. The schema layer is opt-in because
    it is measured at 3.3 minutes for a 174 MB file against 5.8 seconds for the
    profile layer on the same file — a check 34x slower than the alternative is
    not a default, it is a decision. (It was 55.8 minutes until validation
    moved to the streaming parser; the profile layer found the same defect
    throughout.)
    """
    rules = make_rules(profile)
    report = CheckReport(source=str(source), profile=rules.name)

    findings, counts = check_profile(source, rules,
                                     check_references=check_references)
    report.findings.extend(findings)
    report.stats.update(counts)
    if not check_references:
        report.not_checked["references"] = "disabled by the caller"

    if with_schema:
        schema_findings, skipped = check_schema(source, xsd_path,
                                                locate=xsd_locate)
        report.findings.extend(schema_findings)
        if skipped:
            report.not_checked["schema"] = skipped
    else:
        report.not_checked["schema"] = (
            "not requested; pass --xsd (slow: ~3 min for a 174 MB file)")

    if with_readability:
        read_findings, imported = check_readability(source)
        report.findings.extend(read_findings)
        report.stats.update({f"imported_{k}": v for k, v in imported.items()})
    else:
        report.not_checked["readability"] = "not requested; pass --read"

    return report
