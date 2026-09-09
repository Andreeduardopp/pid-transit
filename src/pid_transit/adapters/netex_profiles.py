"""NeTEx dialects, and how to tell them apart from the document itself.

NeTEx is a large standard and every national profile uses a different corner of
it. The importer's two-pass architecture absorbs most of that variation without
branching -- a missing element is simply a lookup that returns nothing. This
module exists for the residue: the places where two profiles say *incompatible*
things, so reading one correctly means knowing which one you have.

Detection is structural, never by name. A codespace or a file name is a label a
producer chose; the shape of the elements is what the reader has to cope with,
and it is what stays true when the label changes. The same rule governs how the
elements are grouped: what belongs together belongs together because the XML
nested it together, never because two ids share a suffix.

Adding a dialect
----------------
A new profile is a new `ProfileAdapter` subclass plus a detection rule -- never
a new ``if profile is ...`` inside a function the other profiles run through.
The importer holds exactly one adapter and consults it at the seams the base
class defines; every one of those answers "nothing to do" by default, so a
dialect that needs no special reading costs nothing and, more to the point,
cannot change how any other feed is read. When a profile needs a seam that does
not exist yet, add the seam to the base class with an inert default and
override it in the one subclass that needs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

# Dates the importer writes when a DayType states no validity window of its own.
# A profile may fill them in from elsewhere, but only when they are still these:
# anything else is something the file actually said.
DEFAULT_START_DATE = "19700101"
DEFAULT_END_DATE = "20991231"

# A rule fires on the shape of most of the journeys, not on the first element
# that matches. Real feeds carry strays, and one journey missing an id must not
# change how the other million are read.
_MAJORITY = 0.5

# The Portuguese profile emits one ServiceCalendarFrame per journey, holding a
# handful of conditions each. A frame with thousands is a whole-network calendar
# -- the Swiss feed has exactly one, with 69 634 conditions -- and grouping it
# serves no profile, so collection stops rather than hold a national calendar in
# memory to answer a question nobody asks of it.
MAX_CONDITIONS_PER_FRAME = 64


class NetexProfile(Enum):
    """The NeTEx dialect a source is written in.

    ``GENERIC`` is the CEN/EPIP-shaped baseline -- standalone ``JourneyPattern``
    elements, ``passingTimes``, journeys that carry their own ``id``. It is what
    this library exports and what the other members are described against.
    """

    GENERIC = "generic"
    SWISS_SKI = "swiss_ski"
    EUROPEAN = "european"
    PORTUGUESE = "portuguese"

    @classmethod
    def coerce(cls, value: "NetexProfile | str | None") -> Optional["NetexProfile"]:
        """Accept a member, its value, or its name; ``None`` means auto-detect."""
        if value is None or isinstance(value, cls):
            return value
        text = str(value).strip().lower()
        for member in cls:
            if text in (member.value, member.name.lower()):
                return member
        valid = ", ".join(m.value for m in cls)
        raise ValueError(f"Unknown NeTEx profile {value!r}; expected one of: {valid}")


@dataclass
class ProfileEvidence:
    """Structural counts gathered during pass 1, used to pick a profile.

    Every field is a count rather than a flag, and the rules in `detect` compare
    them against the journey total: a single stray element must not decide a
    profile, and the ratio is what stays defensible when a feed mixes forms.
    """

    journeys: int = 0
    journeys_without_id: int = 0
    journeys_with_pattern_view: int = 0
    journeys_with_calls: int = 0
    journeys_with_line_ref: int = 0
    journeys_with_pattern_ref: int = 0
    journey_patterns: int = 0
    availability_conditions: int = 0
    service_calendar_frames: int = 0
    codespaces: set = field(default_factory=set)

    def detect(self) -> NetexProfile:
        """Pick the profile these counts describe.

        Ordered most specific first, and **the order is load-bearing**: the
        Portuguese profile also has availability conditions and calls, so it
        would match the Swiss rule if that one were tested first. Each rule
        names the incompatibility that forces the branch -- a rule that does not
        change how the file is read does not belong here, however characteristic
        it looks.
        """
        if not self.journeys:
            return NetexProfile.GENERIC
        most = self.journeys * _MAJORITY

        # Journeys with no id at all cannot be stored or referenced, and the
        # pattern lives inline as a JourneyPatternView instead of being a
        # standalone element. Both have to be worked around together.
        if self.journeys_without_id > most and self.journeys_with_pattern_view > most:
            return NetexProfile.PORTUGUESE

        # Calendars as validity windows plus a day bitmask, and stop times as
        # inline calls rather than TimetabledPassingTime.
        if self.availability_conditions and self.journeys_with_calls > most:
            return NetexProfile.SWISS_SKI

        # The line is named two hops up, by the pattern's Route, and never on
        # the journey itself. The second half stays absolute rather than a
        # ratio: one journey that names its line proves the producer does state
        # it, and then the indirection is not the only reading available.
        if self.journeys_with_pattern_ref > most and not self.journeys_with_line_ref:
            return NetexProfile.EUROPEAN

        return NetexProfile.GENERIC

    def summary(self) -> str:
        """One-line description for the log, so a wrong guess is diagnosable."""
        return (
            f"{self.journeys} journeys "
            f"({self.journeys_without_id} without id, "
            f"{self.journeys_with_pattern_view} with inline pattern view, "
            f"{self.journeys_with_calls} with calls, "
            f"{self.journeys_with_line_ref} with direct LineRef, "
            f"{self.journeys_with_pattern_ref} with pattern ref), "
            f"{self.journey_patterns} journey patterns, "
            f"{self.availability_conditions} availability conditions, "
            f"{self.service_calendar_frames} service calendar frames"
        )


@dataclass
class CalendarFrame:
    """One ServiceCalendarFrame, as pass 1 saw it -- structure only.

    The window, the day types and the availability conditions collected here
    belong together because one frame contained them. That is what makes the
    reading independent of the order the frame's children arrive in: NeTEx lets
    the ``ServiceCalendar`` be a sibling of ``dayTypes`` (the form the CARRIS
    file uses) or their parent, and those two orders deliver the end events the
    other way round. Both close before the frame does, which is when this is
    committed, so both read the same.
    """

    window: Optional[Tuple[Optional[str], Optional[str]]] = None
    day_type_ids: List[str] = field(default_factory=list)
    condition_ids: List[str] = field(default_factory=list)
    overflowed: bool = False

    def add_condition(self, cid: str) -> None:
        """Collect a condition id, up to the per-frame cap.

        Past the cap the frame is marked and emptied rather than truncated: a
        half-collected list would let a profile emit half a calendar and report
        it as a whole one.
        """
        if self.overflowed:
            return
        if len(self.condition_ids) >= MAX_CONDITIONS_PER_FRAME:
            self.overflowed = True
            self.condition_ids.clear()
            return
        self.condition_ids.append(cid)

    def is_per_journey(self) -> bool:
        """Whether this frame has the one-day-type shape a journey pairs with."""
        return not self.overflowed and len(self.day_type_ids) == 1


@dataclass
class CalendarResolution:
    """What a profile made of the calendar frames pass 1 collected.

    `claimed_conditions` are the availability conditions the profile has already
    turned into something; the shared flush leaves those alone instead of
    materialising them as day types a second time.
    """

    exceptions: List[dict] = field(default_factory=list)
    claimed_conditions: set = field(default_factory=set)
    journey_day_types: Optional[List[str]] = None
    notes: List[str] = field(default_factory=list)


class ProfileAdapter:
    """What one NeTEx dialect needs read differently, in one place.

    Every hook here answers "nothing to do", and that is the point: the shared
    importer path is what runs unless a subclass says otherwise, so a new
    dialect cannot change how the existing ones are read. One instance per
    import run -- the pass-2 hooks may carry state.
    """

    profile = NetexProfile.GENERIC

    # -- pass 1 -> flush ---------------------------------------------------
    def resolve_calendar(
        self,
        frames: Sequence[CalendarFrame],
        day_types: Dict[str, dict],
        conditions: Dict[str, tuple],
        journey_count: int,
    ) -> CalendarResolution:
        """Interpret the ServiceCalendarFrames pass 1 grouped.

        May fill *missing* dates in `day_types` in place -- never overwrite one
        the file stated. Everything else it wants done comes back in the result.
        """
        return CalendarResolution()

    # -- pass 2 ------------------------------------------------------------
    def synthesize_journey_id(self, day_type_id: Optional[str],
                              ordinal: int) -> Optional[str]:
        """An id for a journey the file did not name, or None to skip it."""
        return None

    def nested_route_ref(self, sj, qnames: Dict[str, str]) -> Optional[str]:
        """A RouteRef the walk over the journey's direct children cannot see.

        Consulted only while the line is still unresolved, so an implementation
        costs nothing on feeds that name their line directly.
        """
        return None


class SwissSkiAdapter(ProfileAdapter):
    """The SBB/SKI split-file profile.

    Nothing is overridden here, and that is a finding rather than a gap: what
    this profile needs -- availability conditions materialised as day types plus
    one exception per set day bit, stop times read from ``calls`` -- is not
    Swiss-specific. Any feed carrying those elements gets the same reading, so
    it belongs on the shared path. This class is the home for the first thing
    that turns out to be genuinely incompatible with another dialect.
    """

    profile = NetexProfile.SWISS_SKI


class EuropeanAdapter(ProfileAdapter):
    """The French/Fluo family: the line is named by the pattern's Route.

    Also unoverridden, for the same reason. The journey-level indirection this
    profile needs (``LineRef`` -> ``jp_to_line`` -> ``route_to_line``) is a
    fallback chain, not a branch: it fires only when the direct reference is
    absent, so running it for every profile costs a dictionary lookup and can
    never contradict a feed that does name its line.
    """

    profile = NetexProfile.EUROPEAN


class PortugueseAdapter(ProfileAdapter):
    """The IMT/How2Go shape, as measured on the CARRIS example feed.

    Three things this profile does that no other does, and each needs a seam:

    * the ``ServiceJourney`` carries no ``id``, so one has to be built;
    * the ``DayType`` carries weekday flags but no ``ValidBetween`` -- its dates
      are in the ``ServiceCalendar`` that shares its frame;
    * ``AvailabilityCondition``s are single-date calendar exceptions carrying
      ``IsAvailable``, not the Swiss window-plus-bitmask. Read as the latter
      they become day types that are never active and change no service.

    None of this is read from the profile specification, which is not published
    in a form we hold: it is measured on the one live file the NAP serves. See
    ``research/data/perfil_netex_pt/``.
    """

    profile = NetexProfile.PORTUGUESE

    def resolve_calendar(self, frames, day_types, conditions, journey_count):
        res = CalendarResolution()
        order: List[str] = []
        for frame in frames:
            if not frame.is_per_journey():
                # Not the per-journey shape this profile emits. Left to the
                # shared path rather than guessed at.
                continue
            dtid = frame.day_type_ids[0]
            order.append(dtid)
            row = day_types.get(dtid)
            if row is None:
                # No day type to hang the exceptions on; the foreign key would
                # reject them. Nothing is claimed, so the shared path still
                # sees the conditions.
                continue
            if frame.window is not None:
                start, end = frame.window
                if start and row["start_date"] == DEFAULT_START_DATE:
                    row["start_date"] = start
                if end and row["end_date"] == DEFAULT_END_DATE:
                    row["end_date"] = end
            for cid in frame.condition_ids:
                cond = conditions.get(cid)
                if cond is None:
                    continue
                c_from, c_to, bits, available = cond
                if bits or not c_from or c_from != c_to:
                    # A window or a bitmask, not the single-date exception this
                    # profile writes. Reading it as one date would invent the
                    # rest, so it goes to the shared path untouched.
                    continue
                res.exceptions.append({
                    "day_type_id": dtid,
                    "date": c_from,
                    "is_addition": available,
                })
                res.claimed_conditions.add(cid)

        if not order:
            return res
        if len(order) == journey_count:
            res.journey_day_types = order
        else:
            res.notes.append(
                f"{len(order)} per-journey calendar frames for {journey_count} "
                "journeys; cannot pair them by position, journeys will have no "
                "day type"
            )
        return res

    def synthesize_journey_id(self, day_type_id, ordinal):
        # The paired calendar's key is the producer's own per-journey id, so it
        # is stable across re-exports -- better than an ordinal. Without a pair
        # there is nothing stable to build from, and inventing one would create
        # a journey that no second import of the same file could match.
        if not day_type_id:
            return None
        return f"sj:{day_type_id.rsplit(':', 1)[-1]}"

    def nested_route_ref(self, sj, qnames):
        # The RouteRef sits inside JourneyPatternView, and the walk over the
        # journey's direct children cannot see it. CARRIS journeys all carry a
        # direct LineRef, so this costs nothing there -- but a PT feed that
        # omits it would otherwise be dropped whole, which is exactly the defect
        # the journey-level line indirection closed once already.
        q_view = qnames["JourneyPatternView"]
        q_route = qnames["RouteRef"]
        for child in sj:
            if child.tag != q_view:
                continue
            for grandchild in child:
                if grandchild.tag == q_route:
                    return grandchild.attrib.get("ref")
        return None


_ADAPTERS = {
    NetexProfile.GENERIC: ProfileAdapter,
    NetexProfile.SWISS_SKI: SwissSkiAdapter,
    NetexProfile.EUROPEAN: EuropeanAdapter,
    NetexProfile.PORTUGUESE: PortugueseAdapter,
}


def make_adapter(profile: NetexProfile) -> ProfileAdapter:
    """A fresh adapter for one import run; they may carry per-run state."""
    return _ADAPTERS.get(profile, ProfileAdapter)()
