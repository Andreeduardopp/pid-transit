"""
NeTEx Importer Adapter (Object-Oriented).

Streams the XML with iterparse in two passes so peak memory stays flat
regardless of file size: pass 1 collects the (small) reference entities and
lookup maps, pass 2 streams the bulky ServiceJourneys and flushes rows to the
database in batches.
"""

import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from functools import partial
from typing import Dict, List, Optional, Sequence, Tuple, Union
from pathlib import Path

from ..core.database import TransmodelDatabase
from ..core.exceptions import ImportCancelledError

logger = logging.getLogger(__name__)

_DEFAULT_NS = {"n": "http://www.netex.org.uk/netex"}
_GML_NS = "http://www.opengis.net/gml/3.2"

_DURATION_RE = re.compile(
    r"^-?P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)

_DAY_NAMES = {
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
}

BATCH_SIZE = 5000

# NeTEx VehicleMode strings -> internal TransportMode values. NeTEx casing
# and vocabulary differ from ours (trolleyBus, cableway, water, ...); unknown
# modes fall back to "other" so they never fail model validation later.
_NETEX_MODE_TO_INTERNAL = {
    "bus": "bus",
    "coach": "bus",
    "tram": "tram",
    "rail": "rail",
    "urbanRail": "rail",
    "intercityRail": "rail",
    "metro": "metro",
    "underground": "metro",
    "tube": "metro",
    "ferry": "ferry",
    "water": "ferry",
    "trolleyBus": "trolleybus",
    "funicular": "funicular",
    "cableway": "cable_car",
    "cableCar": "cable_car",
    "telecabin": "gondola",
    "gondola": "gondola",
    "monorail": "monorail",
    # Identity mappings for values this library's own exporter emitted
    "trolleybus": "trolleybus",
    "cable_car": "cable_car",
    "other": "other",
}


def _normalize_mode(mode: Optional[str]) -> str:
    if not mode:
        return "bus"
    normalized = _NETEX_MODE_TO_INTERNAL.get(mode)
    if normalized is None:
        logger.info("Unmapped NeTEx TransportMode '%s', storing as 'other'", mode)
        return "other"
    return normalized


def _text(el: Optional[ET.Element]) -> Optional[str]:
    if el is not None and el.text:
        return el.text.strip()
    return None


def _localname(tag: str) -> str:
    return tag.rpartition("}")[2]


class _SourceSet:
    """Resolves an import source into a list of reopenable XML streams.

    Accepts a single .xml file, a directory (all *.xml recursively), a .zip
    archive (members are streamed, never extracted), or a list of .xml paths.
    Each entry can be opened once per pass; the zip handle stays open for the
    whole run.
    """

    def __init__(self, source):
        self._zf: Optional[zipfile.ZipFile] = None
        self.entries: List[Tuple[str, callable]] = []

        if isinstance(source, (list, tuple)):
            for p in source:
                p = Path(p)
                self.entries.append((str(p), partial(open, p, "rb")))
            if not self.entries:
                raise ImportError("Empty list of NeTEx source files")
            return

        path = Path(source)
        if path.is_dir():
            for p in sorted(path.rglob("*.xml")):
                self.entries.append((str(p), partial(open, p, "rb")))
            if not self.entries:
                raise ImportError(f"No .xml files found in directory '{path}'")
        elif path.suffix.lower() == ".zip":
            self._zf = zipfile.ZipFile(path)
            names = sorted(
                n for n in self._zf.namelist() if n.lower().endswith(".xml")
            )
            if not names:
                self._zf.close()
                raise ImportError(f"No .xml members found in zip '{path}'")
            for n in names:
                self.entries.append((f"{path.name}/{n}", partial(self._zf.open, n)))
        else:
            self.entries.append((str(path), partial(open, path, "rb")))

    def __enter__(self) -> "_SourceSet":
        return self

    def __exit__(self, *exc) -> None:
        if self._zf is not None:
            self._zf.close()

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)


def _reconstruct_time(time_str: str, day_offset: int = 0) -> str:
    """Combine xs:time + DayOffset into a GTFS-style HH:MM:SS (may be >= 24)."""
    parts = time_str.strip().split(":")
    h = int(parts[0]) + day_offset * 24
    m = int(parts[1])
    s = int(float(parts[2])) if len(parts) > 2 else 0
    return f"{h:02d}:{m:02d}:{s:02d}"


def _date_from_iso(text: Optional[str]) -> Optional[str]:
    """ISO date(-time) -> GTFS YYYYMMDD, or None."""
    if not text:
        return None
    return text.strip()[:10].replace("-", "")


def _duration_secs(text: Optional[str]) -> Optional[int]:
    """xs:duration (e.g. PT2M30S) -> seconds, or None if unparseable."""
    if not text:
        return None
    m = _DURATION_RE.match(text.strip())
    if not m or not any(m.groups()):
        return None
    days, hours, minutes, seconds = (float(g) if g else 0 for g in m.groups())
    return int(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def _bits_to_exceptions(day_type_id: str, from_date: str, bits: str,
                        is_addition: bool = True) -> List[dict]:
    """ValidDayBits bitstring -> one operating-day exception per set bit."""
    try:
        start = datetime.strptime(from_date, "%Y%m%d").date()
    except (ValueError, TypeError):
        return []
    return [{
        "day_type_id": day_type_id,
        "date": (start + timedelta(days=i)).strftime("%Y%m%d"),
        "is_addition": is_addition,
    } for i, bit in enumerate(bits) if bit == "1"]


class _RunState:
    """Reference maps and cumulative stats shared across the import passes."""

    def __init__(self):
        self.stats: Dict[str, int] = {}
        # pass-1 lookup maps
        self.stop_places: Dict[str, tuple] = {}     # id -> (name, lat, lon, wheelchair)
        self.quays: Dict[str, tuple] = {}           # id -> (name, lat, lon, wheelchair)
        self.assignments: Dict[str, str] = {}       # ssp id -> quay/stop place id
        self.ssp_points: Dict[str, tuple] = {}      # ssp id -> (name, QuayRef attr)
        self.route_to_line: Dict[str, str] = {}     # Route id -> Line id
        self.line_ids: set = set()
        self.day_type_ids: set = set()
        self.jp_to_line: Dict[str, str] = {}        # pattern id -> line id (via SJs)
        # pattern id -> (direction, route_ref, [(order, spi_id, ssp_ref), ...])
        self.patterns: Dict[str, tuple] = {}
        # calendar structures
        self.day_type_assignments: List[tuple] = []  # (dt_ref, date, period_ref, available)
        self.operating_periods: Dict[str, tuple] = {}   # id -> (from, to, bits)
        self.avail_conditions: Dict[str, tuple] = {}    # id -> (from, to, bits)
        # shapes
        self.jp_shape: Dict[str, str] = {}          # pattern id -> shape id
        self.shape_ids: set = set()
        self.shape_points: List[dict] = []
        # pass-1 row buffers (small entities, flushed once)
        self.operators: List[dict] = []
        self.day_types: Dict[str, dict] = {}        # id -> row (dates may be updated)
        self.lines: List[dict] = []
        self.transfers: List[dict] = []
        # built when reference data is flushed, used by pass 2
        self.jp_spi_to_stop: Dict[str, Dict[str, str]] = {}
        self.stop_ids: set = set()      # stops actually imported
        self.pattern_ids: set = set()   # journey patterns actually imported
        # files where pass 1 saw (Template)ServiceJourney elements; pass 2
        # skips the rest (site/resource/calendar files) instead of re-parsing
        self.current_file: Optional[str] = None
        self.files_with_journeys: set = set()

    def add_stat(self, key: str, n: int) -> None:
        if n:
            self.stats[key] = self.stats.get(key, 0) + n


class NetexImporter:
    """
    Adapter class for importing NeTEx XML files into the Transmodel database.
    """

    def __init__(self, fallback_timezone: str = "UTC", on_progress=None, cancel_event=None):
        """
        Args:
            fallback_timezone: timezone assigned to operators (NeTEx has none).
            on_progress: optional callable
                ``(entity_type: str, count: int, *, file_index=None, total_files=None)``
                fired after every batch flush with the cumulative row count for
                that entity type. Suitable for streaming progress to a client.
            cancel_event: optional ``threading.Event``; when set, the import
                aborts between batches with ``ImportCancelledError`` and the
                in-flight transaction is rolled back.
        """
        self.fallback_timezone = fallback_timezone
        self.on_progress = on_progress
        self.cancel_event = cancel_event
        self._ns: Dict[str, str] = dict(_DEFAULT_NS)
        self._file_index: Optional[int] = None
        self._total_files: Optional[int] = None

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ImportCancelledError("NeTEx import cancelled")

    def _report(self, entity_type: str, count: int) -> None:
        if self.on_progress is not None:
            self.on_progress(entity_type, count,
                             file_index=self._file_index,
                             total_files=self._total_files)

    @staticmethod
    def _detect_namespace(root: ET.Element) -> Dict[str, str]:
        tag = root.tag
        if tag.startswith("{"):
            ns_uri = tag.split("}")[0].lstrip("{")
            return {"n": ns_uri}
        return dict(_DEFAULT_NS)

    def import_to_db(
        self, db: TransmodelDatabase, source: Union[Path, str, Sequence[Union[Path, str]]]
    ) -> Dict[str, int]:
        """Import NeTEx data into the database.

        `source` may be a single .xml file, a directory of .xml files, a .zip
        archive, or a list of .xml paths. Pass 1 collects reference data from
        every file before pass 2 streams any service journeys, so entities may
        reference each other across files in any order.
        """
        state = _RunState()
        with db.bulk_session(), _SourceSet(source) as sources:
            n = len(sources)
            self._total_files = n
            pass1_handlers = self._pass1_handlers(state)
            for i, (label, opener) in enumerate(sources, 1):
                self._check_cancel()
                self._file_index = i
                state.current_file = label
                logger.info("NeTEx pass 1 (reference data, file %d/%d): %s", i, n, label)
                with opener() as f:
                    self._stream(f, pass1_handlers, label)
            self._check_cancel()
            self._flush_reference_data(db, state)
            pass2 = _Pass2(self, db, state)
            pass2_handlers = pass2.handlers()
            for i, (label, opener) in enumerate(sources, 1):
                self._check_cancel()
                self._file_index = i
                if label not in state.files_with_journeys:
                    logger.info(
                        "NeTEx pass 2: skipping %s (no service journeys)", label)
                    continue
                logger.info("NeTEx pass 2 (service journeys, file %d/%d): %s", i, n, label)
                with opener() as f:
                    self._stream(f, pass2_handlers, label)
            pass2.flush()
            if pass2.skipped_journeys:
                logger.warning(
                    "Skipped %d service journeys with unresolvable line references",
                    pass2.skipped_journeys,
                )
            if pass2.dropped_call_stops:
                logger.warning(
                    "Dropped %d call passing times referencing unknown stops",
                    pass2.dropped_call_stops,
                )
        return state.stats

    # -------------------------------------------------------------------------
    # Streaming machinery
    # -------------------------------------------------------------------------

    def _stream(self, source, handlers: Dict[str, callable], label: str = "") -> None:
        """iterparse `source`, dispatch end-events for `handlers` tags, and
        prune each handled element so memory stays flat.

        The just-ended element is always the last child of the element on top
        of the stack, so pruning is an O(1) `del parent[-1]` instead of an
        O(n) list removal.
        """
        stack: List[ET.Element] = []
        push, pop = stack.append, stack.pop
        get = None  # bound to the tag-qualified handler dict on the root event
        try:
            for event, elem in ET.iterparse(source, events=("start", "end")):
                if event == "start":
                    if get is None:
                        # Qualify handler keys with the document namespace once,
                        # so dispatch is a plain dict lookup on elem.tag instead
                        # of splitting the namespace off every element.
                        self._ns = self._detect_namespace(elem)
                        if elem.tag.startswith("{"):
                            uri = self._ns["n"]
                            get = {f"{{{uri}}}{k}": v for k, v in handlers.items()}.get
                        else:
                            get = handlers.get
                    push(elem)
                    continue
                pop()
                handler = get(elem.tag)
                if handler is not None:
                    handler(elem)
                    elem.clear()
                    if stack:
                        parent = stack[-1]
                        if len(parent) and parent[-1] is elem:
                            del parent[-1]
        except ET.ParseError as exc:
            raise ImportError(f"Failed to parse NeTEx XML '{label or source}': {exc}") from exc

    def _centroid_coords(self, el: ET.Element, entity_id: str) -> Tuple[Optional[float], Optional[float]]:
        """Extract (lat, lon) from an element's Centroid/Location, or (None, None)."""
        centroid = el.find("n:Centroid", self._ns)
        if centroid is None:
            return None, None
        loc = centroid.find("n:Location", self._ns)
        if loc is None:
            return None, None
        lat_t = _text(loc.find("n:Latitude", self._ns))
        lon_t = _text(loc.find("n:Longitude", self._ns))
        if not (lat_t and lon_t):
            return None, None
        try:
            return float(lat_t), float(lon_t)
        except (ValueError, TypeError):
            logger.warning("Invalid coordinates for %s", entity_id)
            return None, None

    def _access_int(self, el: ET.Element) -> Optional[int]:
        """AccessibilityAssessment/MobilityImpairedAccess -> GTFS wheelchair code."""
        assessment = el.find("n:AccessibilityAssessment", self._ns)
        if assessment is None:
            return None
        value = _text(assessment.find("n:MobilityImpairedAccess", self._ns))
        return {"true": 1, "false": 2}.get(value)

    def _key_value(self, el: ET.Element, key: str) -> Optional[str]:
        """Value of a keyList KeyValue entry with the given Key, or None."""
        for kv in el.findall("n:keyList/n:KeyValue", self._ns):
            if _text(kv.find("n:Key", self._ns)) == key:
                return _text(kv.find("n:Value", self._ns))
        return None

    # -------------------------------------------------------------------------
    # Pass 1: reference entities and lookup maps
    # -------------------------------------------------------------------------

    def _pass1_handlers(self, state: _RunState) -> Dict[str, callable]:
        # Handlers read self._ns at call time; _stream sets it from the first
        # start event, before any handler fires.

        def on_operator(op: ET.Element) -> None:
            oid = op.attrib.get("id", "UNKNOWN")
            name = _text(op.find("n:Name", self._ns)) or "Unnamed"
            phone = None
            contact = op.find("n:ContactDetails", self._ns)
            if contact is not None:
                phone = _text(contact.find("n:Phone", self._ns))
            state.operators.append({
                "id": oid,
                "name": name,
                "timezone": self.fallback_timezone,
                "phone": phone,
            })

        def on_stop_place(sp: ET.Element) -> None:
            sid = sp.attrib.get("id")
            if not sid:
                return
            lat, lon = self._centroid_coords(sp, f"StopPlace {sid}")
            state.stop_places[sid] = (
                _text(sp.find("n:Name", self._ns)), lat, lon, self._access_int(sp),
            )

        def on_quay(q: ET.Element) -> None:
            qid = q.attrib.get("id")
            if not qid:
                return
            lat, lon = self._centroid_coords(q, f"Quay {qid}")
            state.quays[qid] = (
                _text(q.find("n:Name", self._ns)), lat, lon, self._access_int(q),
            )

        def on_assignment(pa: ET.Element) -> None:
            ssp_ref = pa.find("n:ScheduledStopPointRef", self._ns)
            if ssp_ref is None:
                return
            ssp_id = ssp_ref.attrib.get("ref")
            if not ssp_id or ssp_id in state.assignments:
                return
            quay_ref = pa.find("n:QuayRef", self._ns)
            site_ref = quay_ref.attrib.get("ref") if quay_ref is not None else None
            if not site_ref:
                sp_ref = pa.find("n:StopPlaceRef", self._ns)
                site_ref = sp_ref.attrib.get("ref") if sp_ref is not None else None
            if site_ref:
                state.assignments[ssp_id] = site_ref

        def on_ssp(ssp: ET.Element) -> None:
            sid = ssp.attrib.get("id")
            if not sid:
                return
            state.ssp_points[sid] = (
                _text(ssp.find("n:Name", self._ns)),
                ssp.attrib.get("QuayRef"),
            )

        def on_line(line: ET.Element) -> None:
            lid = line.attrib.get("id")
            if not lid:
                return
            op_ref = line.find("n:OperatorRef", self._ns)
            state.line_ids.add(lid)
            state.lines.append({
                "id": lid,
                "operator_id": op_ref.attrib.get("ref") if op_ref is not None else None,
                "name": _text(line.find("n:Name", self._ns)) or "Unnamed",
                "short_name": _text(line.find("n:ShortName", self._ns)),
                "transport_mode": _normalize_mode(
                    _text(line.find("n:TransportMode", self._ns))
                ),
            })

        def on_route(route: ET.Element) -> None:
            rid = route.attrib.get("id")
            lr = route.find("n:LineRef", self._ns)
            if rid and lr is not None and lr.attrib.get("ref"):
                state.route_to_line[rid] = lr.attrib.get("ref")

        def on_day_type(dt: ET.Element) -> None:
            dtid = dt.attrib.get("id")
            if not dtid:
                return
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
            start, end = None, None
            valid_between = dt.find("n:ValidBetween", self._ns)
            if valid_between is not None:
                start = _date_from_iso(_text(valid_between.find("n:FromDate", self._ns)))
                end = _date_from_iso(_text(valid_between.find("n:ToDate", self._ns)))
            state.day_type_ids.add(dtid)
            state.day_types[dtid] = {
                "id": dtid,
                **flags,
                "start_date": start or "19700101",
                "end_date": end or "20991231",
            }

        def on_day_type_assignment(a: ET.Element) -> None:
            dt_ref = a.find("n:DayTypeRef", self._ns)
            if dt_ref is None or not dt_ref.attrib.get("ref"):
                return
            period_ref_el = a.find("n:OperatingPeriodRef", self._ns)
            if period_ref_el is None:
                period_ref_el = a.find("n:UicOperatingPeriodRef", self._ns)
            available = _text(a.find("n:isAvailable", self._ns)) != "false"
            state.day_type_assignments.append((
                dt_ref.attrib.get("ref"),
                _date_from_iso(_text(a.find("n:Date", self._ns))),
                period_ref_el.attrib.get("ref") if period_ref_el is not None else None,
                available,
            ))

        def on_operating_period(p: ET.Element) -> None:
            pid = p.attrib.get("id")
            if not pid:
                return
            state.operating_periods[pid] = (
                _date_from_iso(_text(p.find("n:FromDate", self._ns))),
                _date_from_iso(_text(p.find("n:ToDate", self._ns))),
                _text(p.find("n:ValidDayBits", self._ns)),
            )

        def on_availability_condition(c: ET.Element) -> None:
            # Swiss-profile calendars: a validity window plus a day bitmask,
            # referenced from ServiceJourney validityConditions.
            cid = c.attrib.get("id")
            if not cid:
                return
            state.avail_conditions[cid] = (
                _date_from_iso(_text(c.find("n:FromDate", self._ns))),
                _date_from_iso(_text(c.find("n:ToDate", self._ns))),
                _text(c.find("n:ValidDayBits", self._ns)),
            )

        def on_connection(conn: ET.Element) -> None:
            refs = []
            for end in ("n:From", "n:To"):
                end_el = conn.find(end, self._ns)
                ref_el = end_el.find("n:ScheduledStopPointRef", self._ns) if end_el is not None else None
                refs.append(ref_el.attrib.get("ref") if ref_el is not None else None)
            if not (refs[0] and refs[1]):
                return
            duration = None
            wtd = conn.find("n:WalkTransferDuration", self._ns)
            if wtd is not None:
                duration = _duration_secs(_text(wtd.find("n:DefaultDuration", self._ns)))
            type_text = self._key_value(conn, "gtfs_transfer_type")
            if type_text is not None:
                transfer_type = int(type_text)
            else:
                transfer_type = 2 if duration is not None else 0
            state.transfers.append({
                "from_stop_id": refs[0],
                "to_stop_id": refs[1],
                "transfer_type": transfer_type,
                "min_transfer_time": duration,
            })

        def on_pattern(jp: ET.Element) -> None:
            jpid = jp.attrib.get("id")
            if not jpid:
                return
            direction = _text(jp.find("n:DirectionType", self._ns))
            route_ref_el = jp.find("n:RouteRef", self._ns)
            route_ref = route_ref_el.attrib.get("ref") if route_ref_el is not None else None

            # A JourneyPattern is a LinkSequence: its shape rides along as a
            # projections/LinkSequenceProjection with a gml:posList.
            proj = jp.find("n:projections/n:LinkSequenceProjection", self._ns)
            if proj is not None:
                shape_id = proj.attrib.get("id") or f"{jpid}:shape"
                state.jp_shape[jpid] = shape_id
                if shape_id not in state.shape_ids:
                    pos = proj.find(".//g:posList", {**self._ns, "g": _GML_NS})
                    if pos is not None and pos.text:
                        values = pos.text.split()
                        try:
                            state.shape_points.extend({
                                "shape_id": shape_id,
                                "lat": float(values[i]),
                                "lon": float(values[i + 1]),
                                "sequence": i // 2,
                            } for i in range(0, len(values) - 1, 2))
                            state.shape_ids.add(shape_id)
                        except ValueError:
                            logger.warning("Invalid gml:posList on pattern %s", jpid)
            points = []
            seq = jp.find("n:pointsInSequence", self._ns)
            if seq is not None:
                for spi in seq.findall("n:StopPointInJourneyPattern", self._ns):
                    ssp_ref = spi.find("n:ScheduledStopPointRef", self._ns)
                    if ssp_ref is not None:
                        points.append((
                            int(spi.attrib.get("order", 0)),
                            spi.attrib.get("id"),
                            ssp_ref.attrib.get("ref"),
                        ))
            state.patterns[jpid] = (direction, route_ref, points)

        def on_service_journey(sj: ET.Element) -> None:
            # Light scan only: patterns without their own route/line reference
            # are resolved through the journeys that use them. Also marks the
            # file as journey-bearing so pass 2 knows to re-parse it.
            state.files_with_journeys.add(state.current_file)
            uri = self._ns["n"]
            q_jp = f"{{{uri}}}JourneyPatternRef"
            q_lr = f"{{{uri}}}LineRef"
            jp = lr = None
            for child in sj:
                tag = child.tag
                if tag == q_jp:
                    if jp is None:
                        jp = child.attrib.get("ref")
                elif tag == q_lr:
                    if lr is None:
                        lr = child.attrib.get("ref")
                if jp is not None and lr is not None:
                    break
            if jp and lr and jp not in state.jp_to_line:
                state.jp_to_line[jp] = lr

        return {
            "Operator": on_operator,
            "StopPlace": on_stop_place,
            "Quay": on_quay,
            "PassengerStopAssignment": on_assignment,
            "ScheduledStopPoint": on_ssp,
            "Line": on_line,
            "Route": on_route,
            "DayType": on_day_type,
            "DayTypeAssignment": on_day_type_assignment,
            "OperatingPeriod": on_operating_period,
            "UicOperatingPeriod": on_operating_period,
            "AvailabilityCondition": on_availability_condition,
            "Connection": on_connection,
            "JourneyPattern": on_pattern,
            # Subtypes used by production feeds (e.g. the Swiss profile).
            "ServiceJourneyPattern": on_pattern,
            "ServiceJourney": on_service_journey,
            "TemplateServiceJourney": on_service_journey,
        }

    def _flush_reference_data(self, db: TransmodelDatabase, state: _RunState) -> None:
        if state.operators:
            state.add_stat("operator", db.upsert("operator", state.operators, validate=False))
            logger.info("Imported %d operators", len(state.operators))
            self._report("operator", state.stats["operator"])
            state.operators = []

        stops = self._resolve_stops(state)
        state.stop_ids = {s["id"] for s in stops}
        if stops:
            for i in range(0, len(stops), BATCH_SIZE):
                self._check_cancel()
                state.add_stat(
                    "scheduled_stop_point",
                    db.upsert("scheduled_stop_point", stops[i:i + BATCH_SIZE], validate=False),
                )
                self._report("scheduled_stop_point", state.stats["scheduled_stop_point"])
            logger.info("Imported %d stops", len(stops))

        # Calendar: fold DayTypeAssignments (dates and operating periods) into
        # day-type validity + exceptions, and materialize Swiss-style
        # AvailabilityConditions as all-false day types plus one exception per
        # set day bit (an exact GTFS calendar/calendar_dates encoding).
        exceptions: List[dict] = []
        for dt_ref, date, period_ref, available in state.day_type_assignments:
            if dt_ref not in state.day_types:
                continue
            if date:
                exceptions.append({
                    "day_type_id": dt_ref, "date": date, "is_addition": available,
                })
            elif period_ref and period_ref in state.operating_periods:
                p_from, p_to, bits = state.operating_periods[period_ref]
                if bits and p_from:
                    exceptions.extend(_bits_to_exceptions(dt_ref, p_from, bits, available))
                else:
                    row = state.day_types[dt_ref]
                    if p_from:
                        row["start_date"] = p_from
                    if p_to:
                        row["end_date"] = p_to
        state.day_type_assignments = []

        for cid, (c_from, c_to, bits) in state.avail_conditions.items():
            if cid in state.day_types:
                continue
            state.day_types[cid] = {
                "id": cid,
                **{k: False for k in _DAY_NAMES},
                "start_date": c_from or "19700101",
                "end_date": c_to or "20991231",
            }
            state.day_type_ids.add(cid)
            if bits and c_from:
                exceptions.extend(_bits_to_exceptions(cid, c_from, bits))
        state.avail_conditions = {}

        if state.day_types:
            day_type_rows = list(state.day_types.values())
            for i in range(0, len(day_type_rows), BATCH_SIZE):
                self._check_cancel()
                state.add_stat(
                    "day_type",
                    db.upsert("day_type", day_type_rows[i:i + BATCH_SIZE], validate=False),
                )
            logger.info("Imported %d day types", len(day_type_rows))
            self._report("day_type", state.stats["day_type"])
            state.day_types = {}

        if exceptions:
            for i in range(0, len(exceptions), BATCH_SIZE):
                self._check_cancel()
                state.add_stat(
                    "operating_day_exception",
                    db.upsert("operating_day_exception", exceptions[i:i + BATCH_SIZE], validate=False),
                )
                self._report("operating_day_exception", state.stats["operating_day_exception"])
            logger.info("Imported %d operating day exceptions", len(exceptions))

        if state.transfers:
            transfer_rows = [
                t for t in state.transfers
                if t["from_stop_id"] in state.stop_ids and t["to_stop_id"] in state.stop_ids
            ]
            dropped = len(state.transfers) - len(transfer_rows)
            if dropped:
                logger.warning("Dropped %d transfers referencing unknown stops", dropped)
            for i in range(0, len(transfer_rows), BATCH_SIZE):
                self._check_cancel()
                state.add_stat(
                    "transfer",
                    db.upsert("transfer", transfer_rows[i:i + BATCH_SIZE], validate=False),
                )
            if transfer_rows:
                logger.info("Imported %d transfers", len(transfer_rows))
                self._report("transfer", state.stats["transfer"])
            state.transfers = []

        if state.shape_points:
            for i in range(0, len(state.shape_points), BATCH_SIZE):
                self._check_cancel()
                state.add_stat(
                    "shape_point",
                    db.upsert("shape_point", state.shape_points[i:i + BATCH_SIZE], validate=False),
                )
            logger.info("Imported %d shape points", len(state.shape_points))
            self._report("shape_point", state.stats["shape_point"])
            state.shape_points = []

        if state.lines:
            state.add_stat("line", db.upsert("line", state.lines, validate=False))
            logger.info("Imported %d lines", len(state.lines))
            self._report("line", state.stats["line"])
            state.lines = []

        patterns = []
        points = []
        for jpid, (direction, route_ref, spi_points) in state.patterns.items():
            # A pattern's RouteRef names a Route, which in turn names the Line;
            # minimal feeds (and our own exporter) may reference the Line
            # directly. Fall back to the line seen on this pattern's journeys.
            line_id = None
            if route_ref:
                line_id = state.route_to_line.get(route_ref)
                if not line_id and route_ref in state.line_ids:
                    line_id = route_ref
            if not line_id:
                line_id = state.jp_to_line.get(jpid)
            # Pass 2 needs the SPI -> (stop, order) map even for skipped
            # patterns. Order is converted to 0-based here so passing times
            # imported through the map line up with point_in_journey_pattern.
            state.jp_spi_to_stop[jpid] = {
                spi_id: (ssp_ref, order - 1)
                for order, spi_id, ssp_ref in spi_points if spi_id
            }
            if not line_id or line_id not in state.line_ids:
                logger.warning("JourneyPattern %s has no importable line reference, skipping", jpid)
                continue
            patterns.append({"id": jpid, "line_id": line_id, "direction": direction})
            state.pattern_ids.add(jpid)
            for order, _spi_id, ssp_ref in spi_points:
                if ssp_ref not in state.stop_ids:
                    logger.warning(
                        "JourneyPattern %s references unknown stop %s, dropping point",
                        jpid, ssp_ref,
                    )
                    continue
                points.append({
                    "journey_pattern_id": jpid,
                    "stop_point_id": ssp_ref,
                    "order": order - 1,
                })
        state.patterns = {}

        if patterns:
            for i in range(0, len(patterns), BATCH_SIZE):
                self._check_cancel()
                state.add_stat(
                    "journey_pattern",
                    db.upsert("journey_pattern", patterns[i:i + BATCH_SIZE], validate=False),
                )
                self._report("journey_pattern", state.stats["journey_pattern"])
            logger.info("Imported %d journey patterns", len(patterns))
        if points:
            for i in range(0, len(points), BATCH_SIZE):
                self._check_cancel()
                state.add_stat(
                    "point_in_journey_pattern",
                    db.upsert("point_in_journey_pattern", points[i:i + BATCH_SIZE], validate=False),
                )
                self._report("point_in_journey_pattern", state.stats["point_in_journey_pattern"])

    def _resolve_stops(self, state: _RunState) -> List[dict]:
        stops = []
        if state.ssp_points:
            for sid, (ssp_name, quay_ref_attr) in state.ssp_points.items():
                # Resolution priority: assignment -> Quay/StopPlace, then the
                # SSP's own QuayRef attribute (our exporter emits this), then a
                # StopPlace sharing the SSP id (our exporter's round-trip case).
                site = None
                for ref in (state.assignments.get(sid), quay_ref_attr, sid):
                    if ref:
                        site = state.quays.get(ref) or state.stop_places.get(ref)
                        if site is not None:
                            break
                name = ssp_name or (site[0] if site else None)
                lat, lon, wheelchair = (site[1], site[2], site[3]) if site else (None, None, None)
                if lat is None or lon is None:
                    logger.warning("No coordinates resolved for ScheduledStopPoint %s", sid)
                stops.append({
                    "id": sid, "name": name or "Unnamed", "lat": lat, "lon": lon,
                    "wheelchair_boarding": wheelchair,
                })
        else:
            # No ScheduledStopPoint elements: fall back to importing StopPlaces
            # directly (minimal feeds where patterns reference StopPlace ids).
            for sid, (name, lat, lon, wheelchair) in state.stop_places.items():
                if lat is None or lon is None:
                    logger.warning("No coordinates for StopPlace %s", sid)
                stops.append({
                    "id": sid, "name": name or "Unnamed", "lat": lat, "lon": lon,
                    "wheelchair_boarding": wheelchair,
                })
        return stops


class _Pass2:
    """Streams ServiceJourneys and flushes journey/passing-time batches."""

    def __init__(self, importer: NetexImporter, db: TransmodelDatabase, state: _RunState):
        self.importer = importer
        self.db = db
        self.state = state
        self.journeys: List[dict] = []
        self.passing_times: List[dict] = []
        self.frequencies: List[dict] = []
        self.missing_day_types: set = set()
        self.skipped_journeys = 0
        self.dropped_call_stops = 0
        # namespace-qualified tag cache; rebuilt when a file changes namespace
        self._q_uri: Optional[str] = None
        self._qt: Dict[str, str] = {}

    _TAGS = (
        "DepartureTime", "DepartureDayOffset", "LineRef", "JourneyPatternRef",
        "ServiceJourneyPatternRef", "dayTypes", "DayTypeRef",
        "validityConditions", "AvailabilityConditionRef", "frequencyGroups",
        "passingTimes", "calls", "AccessibilityAssessment",
        "MobilityImpairedAccess", "TimetabledPassingTime",
        "StopPointInJourneyPatternRef", "ArrivalTime", "ArrivalDayOffset",
        "Call", "ScheduledStopPointRef", "Arrival", "Departure", "Time",
        "DayOffset",
    )

    def handlers(self) -> Dict[str, callable]:
        return {
            "ServiceJourney": self.on_service_journey,
            "TemplateServiceJourney": self.on_service_journey,
        }

    def _qtags(self) -> Dict[str, str]:
        uri = self.importer._ns["n"]
        if uri != self._q_uri:
            self._qt = {t: f"{{{uri}}}{t}" for t in self._TAGS}
            self._q_uri = uri
        return self._qt

    @staticmethod
    def _call_time(el: Optional[ET.Element], q_time: str, q_off: str) -> Optional[str]:
        """Time from a Call's Arrival/Departure child (Time + optional DayOffset)."""
        if el is None:
            return None
        t = off = None
        for c in el:
            tag = c.tag
            if tag == q_time:
                t = _text(c)
            elif tag == q_off:
                off = _text(c)
        if not t:
            return None
        return _reconstruct_time(t, int(off) if off else 0)

    def on_service_journey(self, sj: ET.Element) -> None:
        ns = self.importer._ns
        sjid = sj.attrib.get("id")
        if not sjid:
            return
        # One walk over the journey's direct children replaces the dozen
        # find() path evaluations that used to dominate pass-2 CPU.
        Q = self._qtags()

        dep_time_text = dep_offset_text = None
        line_id = jp_ref_id = sjp_ref_id = None
        day_type_id = avail_ref_id = None
        wheelchair = None
        freq_groups = pt_container = calls_el = None

        for child in sj:
            tag = child.tag
            if tag == Q["passingTimes"]:
                if pt_container is None:
                    pt_container = child
            elif tag == Q["calls"]:
                if calls_el is None:
                    calls_el = child
            elif tag == Q["DepartureTime"]:
                if dep_time_text is None:
                    dep_time_text = _text(child)
            elif tag == Q["DepartureDayOffset"]:
                if dep_offset_text is None:
                    dep_offset_text = _text(child)
            elif tag == Q["LineRef"]:
                if line_id is None:
                    line_id = child.attrib.get("ref")
            elif tag == Q["JourneyPatternRef"]:
                if jp_ref_id is None:
                    jp_ref_id = child.attrib.get("ref")
            elif tag == Q["ServiceJourneyPatternRef"]:
                if sjp_ref_id is None:
                    sjp_ref_id = child.attrib.get("ref")
            elif tag == Q["dayTypes"]:
                if day_type_id is None:
                    for r in child:
                        if r.tag == Q["DayTypeRef"]:
                            day_type_id = r.attrib.get("ref")
                            break
            elif tag == Q["validityConditions"]:
                if avail_ref_id is None:
                    for r in child:
                        if r.tag == Q["AvailabilityConditionRef"]:
                            avail_ref_id = r.attrib.get("ref")
                            break
            elif tag == Q["frequencyGroups"]:
                if freq_groups is None:
                    freq_groups = child
            elif tag == Q["AccessibilityAssessment"]:
                if wheelchair is None:
                    for r in child:
                        if r.tag == Q["MobilityImpairedAccess"]:
                            wheelchair = {"true": 1, "false": 2}.get(_text(r))
                            break

        departure_time = _reconstruct_time(
            dep_time_text or "00:00:00",
            int(dep_offset_text) if dep_offset_text else 0)

        jp_id = jp_ref_id or sjp_ref_id

        if day_type_id is None and avail_ref_id is None:
            # Rare path: a DayTypeRef somewhere other than dayTypes/. Only
            # worth the recursive scan when neither standard source matched
            # (Swiss journeys carry an AvailabilityConditionRef instead; those
            # were materialized as day types in pass 1).
            dt_ref = sj.find(".//n:DayTypeRef", ns)
            if dt_ref is not None:
                day_type_id = dt_ref.attrib.get("ref")
        day_type_id = day_type_id or avail_ref_id or "UNKNOWN"

        if not line_id or line_id not in self.state.line_ids:
            # No line, or a line the feed never defines: either would fail the
            # whole executemany batch on the foreign key, so skip this journey.
            self.skipped_journeys += 1
            return

        if day_type_id not in self.state.day_type_ids:
            self.missing_day_types.add(day_type_id)

        self.journeys.append({
            "id": sjid,
            "line_id": line_id,
            # Null out references to patterns that were not imported.
            "journey_pattern_id": jp_id if jp_id in self.state.pattern_ids else None,
            "day_type_id": day_type_id,
            "departure_time": departure_time,
            "wheelchair_accessible": wheelchair,
            "shape_id": self.state.jp_shape.get(jp_id) if jp_id else None,
        })

        if freq_groups is not None:
            for hjg in freq_groups.findall("n:HeadwayJourneyGroup", ns):
                headway = _duration_secs(_text(hjg.find("n:ScheduledHeadwayInterval", ns)))
                start = _text(hjg.find("n:FirstDepartureTime", ns))
                end = _text(hjg.find("n:LastDepartureTime", ns))
                if headway is None or not (start and end):
                    continue
                exact = self.importer._key_value(hjg, "gtfs_exact_times")
                self.frequencies.append({
                    "service_journey_id": sjid,
                    "start_time": start,
                    "end_time": end,
                    "headway_secs": headway,
                    "exact_times": int(exact) if exact else 0,
                })

        if pt_container is not None:
            spi_to_stop = self.state.jp_spi_to_stop.get(jp_id, {}) if jp_id else {}
            q_tpt = Q["TimetabledPassingTime"]
            q_spi = Q["StopPointInJourneyPatternRef"]
            q_arr, q_arr_off = Q["ArrivalTime"], Q["ArrivalDayOffset"]
            q_dep, q_dep_off = Q["DepartureTime"], Q["DepartureDayOffset"]

            order = 0
            for tpt in pt_container:
                if tpt.tag != q_tpt:
                    continue
                stop_id = pattern_order = None
                arr_text = arr_off_text = dep_text = dep_off_text = None
                for c in tpt:
                    tag = c.tag
                    if tag == q_spi:
                        resolved = spi_to_stop.get(c.attrib.get("ref"))
                        if resolved is not None:
                            stop_id, pattern_order = resolved
                    elif tag == q_arr:
                        arr_text = _text(c)
                    elif tag == q_arr_off:
                        arr_off_text = _text(c)
                    elif tag == q_dep:
                        dep_text = _text(c)
                    elif tag == q_dep_off:
                        dep_off_text = _text(c)

                if stop_id:
                    self.passing_times.append({
                        "service_journey_id": sjid,
                        "stop_point_id": stop_id,
                        # Align with the pattern point's order so the two
                        # sequences stay directly comparable (V16 invariant);
                        # positional enumeration is the fallback.
                        "order": pattern_order if pattern_order is not None else order,
                        "arrival_time": _reconstruct_time(
                            arr_text, int(arr_off_text) if arr_off_text else 0
                        ) if arr_text else None,
                        "departure_time": _reconstruct_time(
                            dep_text, int(dep_off_text) if dep_off_text else 0
                        ) if dep_text else None,
                    })
                order += 1
        elif calls_el is not None:
            # Profiles without journey patterns (e.g. the Swiss SKI feeds) put
            # the stop sequence directly on the journey as calls/Call elements
            # with ScheduledStopPointRefs.
            q_call = Q["Call"]
            q_ssp = Q["ScheduledStopPointRef"]
            q_arr_el, q_dep_el = Q["Arrival"], Q["Departure"]
            q_time, q_off = Q["Time"], Q["DayOffset"]
            stop_ids = self.state.stop_ids

            order = 0
            for call in calls_el:
                if call.tag != q_call:
                    continue
                stop_id = arr_el = dep_el = None
                for c in call:
                    tag = c.tag
                    if tag == q_ssp:
                        if stop_id is None:
                            stop_id = c.attrib.get("ref")
                    elif tag == q_arr_el:
                        arr_el = c
                    elif tag == q_dep_el:
                        dep_el = c
                if stop_id and stop_id not in stop_ids:
                    self.dropped_call_stops += 1
                    stop_id = None
                if stop_id:
                    self.passing_times.append({
                        "service_journey_id": sjid,
                        "stop_point_id": stop_id,
                        "order": order,
                        "arrival_time": self._call_time(arr_el, q_time, q_off),
                        "departure_time": self._call_time(dep_el, q_time, q_off),
                    })
                order += 1

        if len(self.passing_times) >= BATCH_SIZE or len(self.journeys) >= BATCH_SIZE:
            self.flush()

    def flush(self) -> None:
        self.importer._check_cancel()
        if self.missing_day_types:
            # Journeys may reference day types the feed never defines (or that
            # live in a file not part of this run). Insert neutral placeholders
            # so the foreign key holds instead of failing the whole batch.
            placeholders = [{
                "id": dtid,
                **{k: False for k in _DAY_NAMES},
                "start_date": "19700101",
                "end_date": "20991231",
            } for dtid in sorted(self.missing_day_types)]
            self.state.add_stat("day_type", self.db.upsert("day_type", placeholders, validate=False))
            self.state.day_type_ids.update(self.missing_day_types)
            self.missing_day_types.clear()
        if self.journeys:
            self.state.add_stat(
                "service_journey",
                self.db.upsert("service_journey", self.journeys, validate=False),
            )
            self.journeys = []
            self.importer._report("service_journey", self.state.stats["service_journey"])
        if self.frequencies:
            self.state.add_stat(
                "frequency",
                self.db.upsert("frequency", self.frequencies, validate=False),
            )
            self.frequencies = []
            self.importer._report("frequency", self.state.stats["frequency"])
        if self.passing_times:
            self.state.add_stat(
                "passing_time",
                self.db.upsert("passing_time", self.passing_times, validate=False),
            )
            self.passing_times = []
            self.importer._report("passing_time", self.state.stats["passing_time"])
