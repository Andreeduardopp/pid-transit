# Database Architecture: GTFS and NeTEx Compatibility

This document explains the architectural decisions behind our database design and how it achieves bidirectional compatibility between GTFS and NeTEx — two fundamentally different transit data standards.

---

## The Problem

GTFS and NeTEx model public transport differently:

| Aspect | GTFS | NeTEx |
|---|---|---|
| Format | Flat CSV files in a .zip | Deeply nested XML |
| Philosophy | Practical, trip-centric | Formal, pattern-centric (Transmodel) |
| Trip modeling | Each trip is standalone with its own stop sequence | Trips reference reusable JourneyPatterns |
| Stop modeling | Single `stops.txt` with `location_type` flag | Separate StopPlace / ScheduledStopPoint concepts |
| Time representation | Linear `HH:MM:SS` (allows ≥ 24:00 for overnight) | `xs:time` + `DayOffset` pair |
| Calendar | Boolean day-of-week flags + date range | DayType with `PropertyOfDay` containing day name strings |
| Naming | `agency`, `route`, `trip`, `stop_time` | `Operator`, `Line`, `ServiceJourney`, `PassingTime` |

A naïve approach — picking one format as the internal model — would mean lossy round-trips in the other direction. We needed a representation that both formats can map to and from without information loss.

---

## Decision 1: Transmodel as the Canonical Model

**Choice:** Use the [Transmodel](https://www.transmodel-cen.eu/) conceptual model (EN 12896) as our internal representation, independent of either serialization format.

**Why:** Transmodel is the European reference data model for public transport. NeTEx is its XML serialization, and GTFS maps cleanly onto a subset of it. By aligning our database with Transmodel concepts, we get:

- A semantically richer model than GTFS alone provides
- Natural compatibility with NeTEx's structure (since NeTEx *is* Transmodel-as-XML)
- Clean mapping paths from GTFS (which covers a well-defined Transmodel subset)
- A stable foundation that doesn't drift with either format's evolution

**Naming convention:** Entities use Transmodel names — `Operator` (not "agency"), `Line` (not "route"), `ServiceJourney` (not "trip"), `PassingTime` (not "stop_time"). This is deliberate: the database speaks Transmodel, and the adapters handle translation.

---

## Decision 2: Adapter Pattern for Format Translation

**Choice:** Isolate all format-specific logic in adapter classes, keeping the core database and domain models completely format-agnostic.

```
GTFS .zip  ←→  GtfsImporter / GtfsExporter  ←→  TransmodelDatabase  ←→  NetexImporter / NetexExporter  ←→  NeTEx XML
                                                         ↕
                                                SpreadsheetImporter  ←→  CSV / XLSX
```

**Why:** Each format has idiosyncrasies that don't belong in the domain model:

- GTFS uses `route_type` integers (0=tram, 3=bus) while NeTEx uses string transport modes (`"bus"`, `"tram"`)
- GTFS `stops.txt` mixes platforms and stations in one file; NeTEx separates them
- NeTEx times need offset reconstruction; GTFS times are plain strings

The adapters own these translations. The core module never sees a GTFS field name or an XML element.

---

## Decision 3: Stop Duality — ScheduledStopPoint vs StopArea

**Choice:** Split the GTFS `stops.txt` concept into two distinct entities:

- **ScheduledStopPoint** — boarding locations where passengers get on/off (`location_type=0`)
- **StopArea** — stations, entrances, generic nodes, boarding areas (`location_type ≥ 1`)

**Why:** GTFS conflates fundamentally different objects into one table using a `location_type` discriminator. Transmodel and NeTEx treat them as separate concepts:

- A `ScheduledStopPoint` is a timetable concept — "the place where a vehicle stops"
- A `StopPlace` (our `StopArea`) is a physical infrastructure concept — "the station building"

Keeping them separate means:

- The GTFS importer can split on `location_type` at import time
- The GTFS exporter can merge them back into `stops.txt` with correct `location_type` values
- The NeTEx exporter can emit proper `StopPlace` elements (in `SiteFrame`) and `ScheduledStopPoint` references (in `ServiceFrame`) without guessing
- The hierarchy (stop → station → entrance → level) is modeled through `StopArea.parent_id` and `StopArea.level_id` foreign keys

**NeTEx import — stops are keyed by `ScheduledStopPoint`, not `StopPlace`.** On real feeds the timetable references `ScheduledStopPointRef`, while coordinates and names live on separate `StopPlace`/`Quay` infrastructure. The importer therefore keys each stop by its SSP id and resolves geometry through a fallback chain: `PassengerStopAssignment` → `Quay` (preferred) or `StopPlace`, then the SSP's own `QuayRef` attribute, then a `StopPlace` sharing the SSP id (the round-trip case, since our exporter emits that attribute). Feeds with no SSP elements fall back to importing `StopPlace`s directly. When no coordinate can be resolved the stop is still imported with NULL lat/lon (see Decision 8) and a warning is logged.

---

## Decision 4: Synthetic JourneyPatterns from GTFS Trips

**Choice:** When importing GTFS, generate one `JourneyPattern` per trip with the ID `JP_{trip_id}`.

**Why:** This is the core semantic gap between the two formats:

- **GTFS** is trip-centric: each trip directly lists its stop sequence in `stop_times.txt`. There is no concept of a reusable pattern.
- **NeTEx** is pattern-centric: trips (ServiceJourneys) *reference* a JourneyPattern that defines the stop sequence. Many trips can share one pattern.

To bridge this, the GTFS importer creates a 1:1 JourneyPattern for each trip. This is intentionally redundant but semantically correct — every trip gets a pattern, even if many patterns are identical.

The redundancy is resolved at export time: the NeTEx exporter has an optional `deduplicate_patterns` flag that groups patterns by `(line_id, direction, ordered_stop_sequence)` and collapses duplicates into canonical patterns. ServiceJourneys are remapped to reference the canonical ID. This means:

- **Import is lossless:** every GTFS trip's stop sequence is preserved exactly
- **Export is clean:** NeTEx output isn't bloated with thousands of identical patterns
- **The database can hold both:** native NeTEx patterns (imported as-is) and synthetic GTFS patterns coexist

**NeTEx import — patternless journeys.** Not every NeTEx feed uses patterns. Swiss SKI-profile `ServiceJourney`s carry their stop sequence inline as `calls/Call` elements with direct `ScheduledStopPointRef`s and no `JourneyPattern` at all. The importer synthesizes a JourneyPattern (and its `PointInJourneyPattern` rows) from the calls, mirroring the GTFS side. Related normalizations on real feeds: `ServiceJourneyPattern` is treated as a `JourneyPattern`, and a pattern's `RouteRef` — which names a `Route`, not a `Line` — is resolved to its line through `Route.LineRef`.

---

## Decision 5: Unified Time Representation

**Choice:** Store all times as GTFS-style `HH:MM:SS` strings (allowing values ≥ 24:00:00 for overnight service).

**Why:** Transit times present a particular challenge:

- **GTFS** allows `25:30:00` to mean "1:30 AM on the next day" — simple and unambiguous
- **NeTEx** uses `01:30:00` + `DayOffset=1` — the same information, split across two fields

We chose the GTFS representation internally because:

- It's a single field, simpler to store and query
- Sorting works lexicographically for most practical cases
- Converting to NeTEx format is mechanical: `hours // 24` gives the offset, `hours % 24` gives the time

The `_reconstruct_time()` function (NeTEx import) and `_normalize_time()` function (NeTEx export) handle this translation.

---

## Decision 6: DayType Calendar Bridging

**Choice:** Store calendar information as boolean day-of-week flags with a date range (matching GTFS `calendar.txt` structure), plus exception records.

**Why:** GTFS and NeTEx represent service calendars differently:

- **GTFS:** `calendar.txt` has boolean columns (`monday=1, tuesday=1, ...`) with `start_date`/`end_date`
- **NeTEx:** `DayType` elements contain `PropertyOfDay` with a space-separated string like `"Monday Tuesday Wednesday"`, and validity is carried by `ValidBetween` / `DayTypeAssignment` (with `OperatingPeriod` / `UicOperatingPeriod`)

The boolean-flag model was chosen because:

- It maps directly to GTFS without transformation
- NeTEx day names are trivially converted to/from flags
- SQL queries on specific days are straightforward (`WHERE monday = 1`)
- The `OperatingDayException` table handles both GTFS `calendar_dates.txt` and NeTEx operating day exceptions

**Edge case — missing calendars:** Some GTFS feeds define service only through `calendar_dates.txt` without corresponding `calendar.txt` entries. The importer handles this by creating placeholder DayTypes with all days set to `False` and the date range derived from the exception dates.

**Edge case — NeTEx `AvailabilityCondition` calendars:** The Swiss SKI profile (and similar national feeds) express service not as `DayType` day-name strings but as `AvailabilityCondition`s — a `FromDate` plus a `ValidDayBits` bitmask — referenced from a journey's `validityConditions`. The importer maps each such condition to an all-`False` DayType plus one `OperatingDayException` per set bit in the bitmask, reusing the same exception machinery as `calendar_dates.txt`.

---

## Decision 7: PointInJourneyPattern as a Join Table

**Choice:** Model the stop sequence as a separate `PointInJourneyPattern` entity with `(journey_pattern_id, stop_point_id, order)`, rather than embedding stop lists in the JourneyPattern.

**Why:** This directly mirrors Transmodel's `PointInJourneyPattern` concept and solves the impedance mismatch between GTFS's flat `stop_times.txt` and NeTEx's nested `pointsInSequence`:

- GTFS `stop_times` is a flat table with `(trip_id, stop_id, stop_sequence)` — the join table maps naturally from this
- NeTEx has `StopPointInJourneyPattern` elements nested inside each `JourneyPattern` — the join table can generate these on export
- The `order` field uses 0-based indexing internally (matching GTFS convention), and the NeTEx exporter converts to 1-based when writing XML

---

## Decision 8: SQLite with Referential Integrity

**Choice:** SQLite with `PRAGMA foreign_keys = ON`, supporting both file-backed and in-memory modes.

**Why:**

- **Portable:** single-file database, no server process, works everywhere
- **Referential integrity catches errors early:** a `ServiceJourney` referencing a nonexistent `DayType` fails at insert time, not at export time when it's harder to diagnose (on real feeds, genuinely dangling references — a line/stop/day type the feed never defines — are skipped or nulled with a warning rather than aborting a whole insert batch)
- **In-memory mode:** used for testing and ephemeral operations (import → transform → export pipelines)
- **Schema versioning:** the `_meta` table tracks `schema_version = "3.1.0-transmodel"` and the database auto-migrates on open. The `3.0 → 3.1` migration made `scheduled_stop_point.lat/lon` nullable (rebuilding the table) so stops with no resolvable coordinates can be imported instead of dropped
- **Bulk-write path for imports:** `bulk_session()` pins one connection for the run with `journal_mode=WAL` / `synchronous=NORMAL` (file DBs), and `upsert()` uses a single prepared `INSERT OR REPLACE` + `executemany` per batch, with an optional `validate=False` fast path for adapter-built rows

---

## Decision 9: Streaming, Multi-File NeTEx Import

**Choice:** Import NeTEx by streaming the XML with a two-pass `iterparse` (pruning parsed elements) rather than loading whole documents into memory, and accept a single file, a directory, a `.zip`, or a list of paths as one logical feed.

**Why:** Production NeTEx feeds are large and split across many files. The Swiss national timetable is 355 files / ~4 GB of XML; loading it as a DOM is not viable, and its cross-file references mean file order can't be assumed.

- **Two passes.** Pass 1 sweeps *all* files collecting reference data (operators, lines, routes, stops/assignments, day types, patterns). Pass 2 then streams `ServiceJourney`s, so a journey can always resolve references defined in any other file. Parsed elements are pruned (`del parent[-1]`) to keep peak memory flat — the just-ended element is always its parent's last child, so pruning is O(1).
- **Any source shape.** A directory, a zip (members are streamed, never extracted to disk), or an explicit path list all import as one feed.
- **Client-friendly.** The importer accepts `on_progress(entity_type, count, *, file_index, total_files)` (fired per batch flush with cumulative counts — SSE-friendly) and a `cancel_event` (`threading.Event`) that aborts cleanly between batches with `ImportCancelledError`, rolling back the in-flight transaction. `GtfsImporter` exposes the same hooks.

The result: the Swiss feed imports with ~1.25 GB peak RSS (down from loading everything), where the previous DOM-based importer produced zero passing times on it. See [`../IMPROVEMENTS.md`](../IMPROVEMENTS.md) for benchmark detail.

---

## Entity Map: Transmodel ↔ GTFS ↔ NeTEx

| Transmodel Entity | GTFS Equivalent | NeTEx Equivalent |
|---|---|---|
| Operator | `agency.txt` | `Operator` |
| Line | `routes.txt` | `Line` |
| ScheduledStopPoint | `stops.txt` (location_type=0) | `ScheduledStopPoint` / `StopPlace` |
| StopArea | `stops.txt` (location_type≥1) | `StopPlace` (station hierarchy) |
| DayType | `calendar.txt` | `DayType` + `PropertyOfDay` |
| OperatingDayException | `calendar_dates.txt` | Operating day exceptions |
| JourneyPattern | *(synthetic from trips)* | `JourneyPattern` |
| PointInJourneyPattern | *(derived from stop_times)* | `StopPointInJourneyPattern` |
| ServiceJourney | `trips.txt` | `ServiceJourney` |
| PassingTime | `stop_times.txt` | `TimetabledPassingTime` |
| Level | `levels.txt` | — |
| Pathway | `pathways.txt` | — |
| ShapePoint | `shapes.txt` | `LinkSequenceProjection` + `gml:posList` (per JourneyPattern) |
| Frequency | `frequencies.txt` | `TemplateServiceJourney` + `HeadwayJourneyGroup` |
| Transfer | `transfers.txt` | `Connection` + `WalkTransferDuration` |
| FareAttribute | `fare_attributes.txt` | — |
| FareRule | `fare_rules.txt` | — |
| Translation | `translations.txt` | — |
| FeedInfo | `feed_info.txt` | Feed metadata |
| Attribution | `attributions.txt` | — |

Shapes, frequencies, and transfers now round-trip through NeTEx in **both** directions (import and export), not just to GTFS. Wheelchair accessibility maps to `AccessibilityAssessment` / `MobilityImpairedAccess` on `StopPlace`, `Quay`, and `ServiceJourney`. `bikes_allowed` has no clean NeTEx home and stays GTFS-only.

---

## Data Flow Example

### GTFS → Database → NeTEx

1. `GtfsImporter` reads `agency.txt` → creates `Operator` records
2. `stops.txt` rows are split by `location_type`: 0 → `ScheduledStopPoint`, ≥1 → `StopArea`
3. Each trip in `trips.txt` gets a synthetic `JourneyPattern` (`JP_{trip_id}`)
4. `stop_times.txt` populates both `PointInJourneyPattern` (stop sequence) and `PassingTime` (arrival/departure)
5. On NeTEx export, the deduplicator collapses identical JourneyPatterns
6. Times are normalized from `25:30:00` → `(01:30:00, DayOffset=1)`
7. The multi-frame XML structure (ResourceFrame, SiteFrame, ServiceFrame, ServiceCalendarFrame, TimetableFrame) is assembled

### NeTEx → Database → GTFS

1. `NetexImporter` streams the source (file / directory / zip / path list), detecting the NeTEx namespace automatically. Pass 1 collects reference data from every file
2. Stops are keyed by `ScheduledStopPoint` id; coordinates and names are resolved through `PassengerStopAssignment` → `Quay`/`StopPlace` (with `QuayRef`-attribute and same-id fallbacks). Unresolvable coordinates are stored as NULL
3. Calendars are parsed from `DayType` day-of-week strings **or**, on profiles that use them, from `AvailabilityCondition` bitmasks (all-false DayType + one exception per set bit)
4. Pass 2 streams `ServiceJourney`s: `TimetabledPassingTime` offsets are reconstructed (`01:30:00 + DayOffset=1` → `25:30:00`), batches flush every 5,000 rows
5. Journeys with a `JourneyPattern` reuse its `pointsInSequence` directly; patternless journeys (inline `calls/Call`) get a synthesized pattern
6. On GTFS export, Transmodel entities are mapped back to the flat CSV structure
