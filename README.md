# PID-Transit

A professional transit data library based on the Transmodel / NeTEx conceptual model. It allows companies to easily create a relational transit database, interact with data using strongly-typed objects (Pydantic), and translate feeds seamlessly between GTFS and NeTEx formats.

## Features

- **Transmodel Core Database**: Underlying SQLite database modeled accurately against European transit standards (Transmodel).
- **Object-Oriented API**: Manage your datasets via the `TransitDataset` facade and clean Repository classes with filtered queries and incremental updates.
- **Strong Typing**: Full Pydantic v2 validation for all 20 entity types (`Operator`, `Line`, `ScheduledStopPoint`, `Pathway`, `ServiceJourney`, `ShapePoint`, `Transfer`, `FareAttribute`, etc.).
- **Query & Filtering**: Filtered queries, lookups by ID, field-based search, counting, and ordered results across all repositories.
- **Full GTFS Coverage**: Imports and exports 17 GTFS files, including `shapes.txt`, `frequencies.txt`, `transfers.txt`, `pathways.txt`, `levels.txt`, `translations.txt`, `attributions.txt`, `fare_attributes.txt`, `fare_rules.txt`, `feed_info.txt`, and accessibility fields (`wheelchair_boarding`, `wheelchair_accessible`, `bikes_allowed`).
- **NeTEx Round-Trip**: Full import and export of NeTEx XML, validated against the CEN NeTEx 1.3 XSD with **zero schema errors**, with journey pattern deduplication and DayOffset handling for overnight services.
- **Analytics**: Built-in operational statistics (headways, vehicle-hours, service spans, stop coverage), feed comparison/diff, and GTFS-RT readiness checks.
- **GTFS-Realtime**: Fetch and parse TripUpdate / VehiclePosition / Alert feeds, then match them against the static dataset to report trip and stop resolution rates and delay distributions.
- **Multi-Feed Merge**: Combine feeds from different operators with ID namespacing and Haversine-based stop deduplication.
- **CLI Tool**: Thirteen commands — `import`, `export`, `convert` (feed file straight to feed file), `validate`, `netex-check`, `stats`, `diff`, `merge`, `normalize`, `frequencies`, `roundtrip`, and the `rt-*` real-time checks.
- **Spreadsheet Import**: Load transit data directly from Excel workbooks or CSV folders.
- **Data Validation**: 20 rules over the Transmodel database — V01–V15 structural (FK integrity, required fields, passing-time order) and V16–V20 semantic (implied-speed plausibility, duplicate stops, orphan inventory, frequency sanity), filterable by severity.
- **NeTEx Profile Detection**: National profiles differ enough to break naive readers. The importer identifies the dialect from the document's structure and adapts — CEN/EPIP-generic, Swiss SKI, the French/European indirection, and the Portuguese IMT profile. See [NeTEx profiles](#netex-profiles).
- **Streaming Import**: Two-pass `iterparse` with element pruning — peak memory stays flat regardless of feed size. See [Performance](#performance).

## Installation

```bash
pip install pid-transit          # core
pip install pid-transit[rt]      # + GTFS-Realtime support
```

*Python 3.10 through 3.14. Runtime dependencies are deliberately minimal — `pydantic>=2.0`
and `openpyxl>=3.0`. GTFS-Realtime support is the optional `rt` extra; the library imports
without it.*

## Performance

Measured, not estimated. Every figure below comes from a timed run on a named feed; the
method and the full result set live in `research/benchmark/METRICS.md`.

**How these were taken.** Each phase runs in its own process, sequentially, on an
otherwise idle machine. *Wall* is `time.perf_counter` around the adapter call, *CPU* is
`time.process_time`, and *peak RSS* is the process high-water mark (`psutil.peak_wset`),
which includes the ~30 MB CPython floor. Short phases are repeated and averaged; long
ones run once. Hardware: Intel Core Ultra 7 265H (16 cores), 31.5 GB RAM, NVMe SSD,
Windows 11, CPython 3.12.1, single-threaded throughout.

**Database size and peak memory are different numbers and are reported separately.** A
feed that produces an 8.6 GB database is still imported inside 1.2 GB of RAM.

### Import

| Feed | Input | Wall | CPU | Peak RSS | Database |
|---|---|---:|---:|---:|---:|
| Porto GTFS | 5.1 MB zip | 32.1 s | 29.0 s | 557 MB | 104.4 MB |
| Porto NeTEx (own dialect) | 151.4 MB XML | 38.1 s | 34.5 s | **80 MB** | 57.9 MB |
| Carris NeTEx (IMT, PT profile) | 173.6 MB XML | 21.9 s | 18.9 s | **149 MB** | 31.9 MB |
| Swiss NeTEx subset (6 files) | 630 MB XML | 251.4 s | 195.9 s | 1,171 MB | 516.6 MB |
| Swiss NeTEx full (355 files) | 420 MB zip / **24.5 GB XML** | 6,501 s (108.4 min) | 2,751 s | **1,171 MB** | 8.64 GB |

The last two rows are the point of the table: input grew **39×** (630 MB → 24.5 GB) and
peak memory did not move at all. Importers stream — a two-pass `iterparse` with element
pruning, flushing to SQLite every 5,000 rows — so memory tracks the lookup maps, not the
feed. The full Swiss run is disk-bound rather than CPU-bound (CPU is 42% of wall) once
the 8.6 GB database outgrows the page cache.

Swiss full feed output: 1.66 M service journeys, 26.5 M passing times, 67,183 stops,
3.7 M calendar exceptions.

### Export

| Feed | Source | Wall | CPU | Peak RSS | Output |
|---|---|---:|---:|---:|---:|
| Porto → GTFS | 104.4 MB db | 12.7 s | 12.2 s | 646 MB | 5.0 MB zip |
| Porto → NeTEx | 104.4 MB db | 45.7 s | 42.7 s | 958 MB | 151.4 MB XML |
| Swiss subset → GTFS | 516.6 MB db | 31.7 s | 30.8 s | 2,900 MB | 14.4 MB zip |
| Swiss subset → NeTEx | 516.6 MB db | 156.6 s | 146.8 s | **5,238 MB** | 1.18 GB XML |

**Known limitation, stated plainly:** the exporters buffer their whole output before
writing, so export memory scales with output size — 5.2 GB peak for a 1.18 GB NeTEx file,
against 1.17 GB flat to import the same data. A full-Swiss export would exhaust 32 GB of
RAM. Import at national scale today; budget accordingly on large exports. Streaming
exporters are the next performance item on the roadmap.

### Startup

`import pid_transit` costs **27.7 ms** and loads 29 modules (was 649.4 ms / 453 modules
before the public API was made lazy). Neither `openpyxl` nor `urllib.request` is pulled
in by a bare import — each loads only when the capability that needs it is used, and a
test asserts it in a fresh interpreter.

## Conformance

Three different claims, deliberately kept apart, because they have different strengths:
whether output is **schema-valid**, whether a round-trip is **lossless**, and whether the
data is **semantically consistent**.

### Schema validity — external validators, not our own opinion

| Target | Validator | Result |
|---|---|---|
| Exported GTFS | MobilityData `gtfs-validator` v8.0.0 | **PASS, 0 errors** (5,603 warnings vs 5,599 in the source feed) |
| Exported NeTEx | CEN NeTEx 1.3 XSD | **PASS, 0 errors** |
| Source GTFS (control) | MobilityData `gtfs-validator` v8.0.0 | PASS, 0 errors, 5,599 warnings |

The warning-profile match matters more than the error count: an exported feed that
reproduces the original's warnings almost exactly (5,603 vs 5,599) is preserving what the
operator supplied rather than silently normalising it. A system of record should not
"improve" its input.

Conformance is re-validated per release rather than assumed. The 0.7.0 re-check caught the
NeTEx exporter having drifted to 2,771 schema errors through changes made for third-party
interoperability that were never re-validated; all were fixed, and the regression tests
that now cover them are the reason the claim above can be made. The full audit trail is in
`research/benchmark/RESULTS.md`.

Re-run it yourself:

```bash
python research/validator/run_validation.py --input path/to/feed.zip
```

*(The XSD step needs `lxml`, which is a harness dependency — the library itself still
depends only on `pydantic` and `openpyxl`.)*

### Round-trip fidelity

GTFS → NeTEx → GTFS is lossless. With pattern deduplication disabled, **all ten entity
counts match the source exactly**, including 91,850 shape points reassembled from per-hop
`ServiceLink` geometry. `tests/test_roundtrip.py` guards this in both directions on every
commit.

Two documented exceptions: `bikes_allowed` is GTFS-only and has no NeTEx equivalent, and
shape cut positions are approximated when a polyline is split per hop (the cut is
reversible; the positions are chosen, not recovered).

### Semantic validation

Schema validity says a file parses. It does not say the data makes sense. `dataset.validate()`
runs 20 rules over the imported database — V01–V15 structural, V16–V20 semantic (implied
travel speed per mode, pattern/passing-time agreement, duplicate stops within 10 m, orphan
inventory, frequency sanity) — filterable by severity so a CI gate can fail on errors
while tolerating advisories. Details in [Data Validation](#data-validation).

## Quickstart

The main entry point for the library is the `TransitDataset` class. Here is a full example of creating a database, adding data programmatically, and querying it:

```python
from pid_transit import TransitDataset, Operator, Line, TransportMode

# 1. Initialize dataset (creates 'my_city.db' if it doesn't exist)
dataset = TransitDataset("my_city.db")

# 2. Add an Operator programmatically
dataset.operators.add(Operator(
    id="OP_001",
    name="City Transit",
    timezone="Europe/Lisbon"
))

# 3. Add a Line
dataset.lines.add(Line(
    id="L_10",
    operator_id="OP_001",
    name="Downtown Loop",
    transport_mode=TransportMode.BUS
))

# 4. Fetch all lines
for line in dataset.lines.get_all():
    print(line.name)
```

## Querying Data

Every repository supports filtered queries, lookups, and counting:

```python
# Lookup by ID (raises EntityNotFoundError if not found)
line = dataset.lines.get_by_id("L_10")

# Filter by field
bus_lines = dataset.lines.get_by_operator("OP_001")

# Filtered query with ordering and limit
journeys = dataset.service_journeys.query(
    where={"line_id": "L_10"},
    order_by="departure_time",
    limit=20
)

# Count records
total_stops = dataset.scheduled_stop_points.count()
weekday_trips = dataset.service_journeys.count(where={"day_type_id": "WEEKDAY"})

# Get ordered stop sequence for a journey pattern
stops_in_pattern = dataset.points_in_journey_pattern.get_by_pattern("JP_001")

# Get passing times for a specific trip
times = dataset.passing_times.get_by_journey("T_001")

# Get shape points for a route geometry
shape = dataset.shape_points.get_by_shape("SH_001")

# Get transfers from a stop
transfers = dataset.transfers.get_by_stop("S_001")
```

### Specialized Repository Methods

| Repository | Method | Description |
|---|---|---|
| `lines` | `get_by_operator(operator_id)` | Lines operated by a specific operator |
| `journey_patterns` | `get_by_line(line_id)` | Journey patterns for a line |
| `points_in_journey_pattern` | `get_by_pattern(pattern_id)` | Ordered stop sequence |
| `service_journeys` | `get_by_line(line_id)` | Trips for a line |
| `service_journeys` | `get_by_day_type(day_type_id)` | Trips for a service day |
| `passing_times` | `get_by_journey(journey_id)` | Ordered stop times for a trip |
| `operating_day_exceptions` | `get_by_day_type(day_type_id)` | Calendar exceptions for a service |
| `shape_points` | `get_by_shape(shape_id)` | Ordered points for a shape |
| `shape_points` | `get_shape_ids()` | List all distinct shape IDs |
| `frequencies` | `get_by_journey(journey_id)` | Frequency entries for a trip |
| `transfers` | `get_by_stop(stop_id)` | Transfers departing from a stop |

### Base Repository Methods

All repositories inherit these common methods:

| Method | Description |
|---|---|
| `get_all()` | Fetch all records as Pydantic models |
| `get_by_id(id)` | Fetch a single entity by primary key (raises `EntityNotFoundError`) |
| `get_by_field(field, value)` | Fetch all entities matching a field value |
| `query(where, order_by, limit)` | Filtered query with optional ordering and limit |
| `count(where)` | Count records, optionally filtered |
| `add(entity)` | Insert or replace a single entity |
| `add_many(entities)` | Batch insert/replace multiple entities |
| `delete(entity_id)` | Delete by ID, returns `True` if deleted |
| `delete_many(entity_ids)` | Delete multiple by ID, returns count deleted |
| `update(entity_id, updates)` | Partial update with Pydantic re-validation |

## Importing and Exporting Data

PID-Transit uses an **Adapter Pattern** for data ingestion and extraction. This allows you to configure specific importers or even write your own.

### Importing a GTFS Feed

```python
from pid_transit import TransitDataset, GtfsImporter

dataset = TransitDataset("my_city.db")
importer = GtfsImporter(strict_mode=True, fallback_timezone="Europe/Lisbon")

stats = dataset.import_data(importer, "path/to/feed.zip")
print(stats)
# {'operator': 1, 'line': 71, 'scheduled_stop_point': 2550,
#  'shape_point': 45000, 'transfer': 120, ...}
```

### Importing NeTEx Data

```python
from pid_transit import TransitDataset, NetexImporter

dataset = TransitDataset("my_city.db")
importer = NetexImporter(fallback_timezone="Europe/Lisbon")

stats = dataset.import_data(importer, "path/to/netex.xml")   # single file
stats = dataset.import_data(importer, "path/to/netex_dir/")  # directory of .xml
stats = dataset.import_data(importer, "path/to/dataset.zip") # zip (streamed)
stats = dataset.import_data(importer, ["a.xml", "b.xml"])    # explicit list
```

The NeTEx importer parses all standard frames: `ResourceFrame` (operators), `SiteFrame` (stops, quays, accessibility), `ServiceCalendarFrame` (day types, validity, day-type assignments, operating periods, availability conditions), `ServiceFrame` (lines, routes, journey patterns, stop assignments, connections/transfers, shape projections), and `TimetableFrame` (service journeys, template journeys with frequencies, passing times or calls). It correctly handles `DayOffset` elements for overnight services (times >= 24:00:00).

Multi-file datasets are imported in two streaming passes (reference data first, then service journeys), so entities may reference each other across files in any order and peak memory stays flat regardless of feed size. Stop coordinates resolve through `PassengerStopAssignment` → `Quay`/`StopPlace`, matching production feeds such as the Swiss national timetable.

#### NeTEx profiles

NeTEx is a large standard, and national profiles use different corners of it. Two feeds
can both be valid CEN NeTEx and still need different readers. The importer identifies the
dialect from the **structure of the document** — never from a codespace or a file name,
which are labels a producer chose and can change — and adapts:

| Profile | Recognised by | What it needs |
|---|---|---|
| `GENERIC` | Standalone `JourneyPattern`, `passingTimes`, journeys with their own `id` | CEN/EPIP baseline — what this library exports |
| `SWISS_SKI` | `AvailabilityCondition` calendars plus inline `calls` | Validity windows with day bitmasks instead of `DayType` |
| `EUROPEAN` | Journeys naming only a pattern, never a line | Line resolved two hops up, via the pattern's `Route` |
| `PORTUGUESE` | Journeys with **no `id` at all** plus an inline `JourneyPatternView` | Synthesised journey ids, and calendars paired by document order |

```python
importer = NetexImporter()                     # detect (default)
importer = NetexImporter(profile="portuguese") # or force it
dataset.import_data(importer, "netex.zip")
print(importer.last_profile)                   # NetexProfile.PORTUGUESE
```

Detection is logged at INFO with the evidence behind it, so a wrong guess is diagnosable
rather than mysterious, and `profile=` overrides it outright.

**Worked example — the Portuguese national profile.** The IMT's published Carris dataset
(173.6 MB of XML) writes `ServiceJourney` elements carrying *no attributes at all*, an
inline `JourneyPatternView` instead of a `JourneyPattern`, and one `ServiceCalendarFrame`
per journey with no reference between the two. Read as generic NeTEx, the network imports
and the entire timetable silently disappears. Read as `PORTUGUESE`, all 10,238 journeys
and 296,603 passing times import with real calendars, in 21.9 s at 149 MB peak RSS.

Where a profile forces an inference rather than a reading, the importer says so and
refuses to guess. The Portuguese journey-to-calendar pairing is positional — the only
interpretation the file permits — so it is applied **only** when the two counts match
exactly; otherwise it warns and imports the journeys without a calendar rather than
attaching a plausible wrong one. Journeys that genuinely cannot be read are counted and
reported, never dropped in silence.

For long-running conversions the importers accept a progress callback and a cancel event (both optional, also on `GtfsImporter`):

```python
import threading

cancel = threading.Event()

def on_progress(entity_type, count, *, file_index=None, total_files=None):
    print(f"{entity_type}: {count:,} (file {file_index}/{total_files})")

importer = NetexImporter(on_progress=on_progress, cancel_event=cancel)
# cancel.set() from another thread aborts between batches
# with pid_transit.core.exceptions.ImportCancelledError
```

### Exporting to GTFS

```python
from pid_transit import TransitDataset, GtfsExporter

dataset = TransitDataset("my_city.db")
exporter = GtfsExporter(include_shapes=True)

dataset.export_data(exporter, "output_feed.zip")
```

The exporter writes 17 GTFS files: `agency.txt`, `routes.txt`, `stops.txt`, `calendar.txt`, `calendar_dates.txt`, `trips.txt`, `stop_times.txt`, `shapes.txt`, `frequencies.txt`, `transfers.txt`, `pathways.txt`, `levels.txt`, `translations.txt`, `attributions.txt`, `fare_attributes.txt`, `fare_rules.txt`, and `feed_info.txt`. Accessibility fields (`wheelchair_boarding`, `wheelchair_accessible`, `bikes_allowed`) are included automatically when present.

### Exporting to NeTEx

```python
from pid_transit import TransitDataset, NetexExporter

dataset = TransitDataset("my_city.db")
exporter = NetexExporter(
    profile="EPIP",             # accepted but currently inert — see note below
    deduplicate_patterns=True,  # reduces output size significantly
    compress=False,             # set True for .gz output
)

dataset.export_data(exporter, "output_netex.xml")
```

> **Note:** `profile` is accepted but has no effect yet. The exporter emits generic CEN
> NeTEx 1.3, not a profile-constrained document. Profile-aware export (EPIP and national
> profiles) is planned, not shipped — if your National Access Point enforces a profile,
> validate the output against that profile before submitting.

The `deduplicate_patterns` option groups journey patterns with identical stop sequences (same line, direction, and ordered stops), writing only canonical patterns and remapping references. This can reduce NeTEx output from thousands of duplicate patterns to a few hundred unique ones.

The export includes calendar validity (`ValidBetween`) and exceptions (`DayTypeAssignment`), shapes (`LinkSequenceProjection` with `gml:posList` on the journey pattern), transfers (`Connection`), frequencies (`TemplateServiceJourney`/`HeadwayJourneyGroup`), and wheelchair accessibility (`AccessibilityAssessment`) — all of which the NeTEx importer reads back, so GTFS → NeTEx → GTFS round-trips are lossless for these fields (`bikes_allowed` is GTFS-only).

### Converting a feed in one call

Import-then-export is the general path, and it is the right one when the database is also
going to be validated, merged or queried. When all you want is *this file, in that
format*, `pid_transit.convert` is that pipeline with the database kept in memory:

```python
from pid_transit import gtfs_to_netex, netex_to_gtfs, convert

# Target defaults to <source>.netex.xml, written beside the source
result = gtfs_to_netex("feed.zip")
print(result.target)              # feed.netex.xml

# Or name it, and choose the profile to write
result = gtfs_to_netex("feed.zip", "carris.xml", profile="pt")
result = netex_to_gtfs("netex.zip", "feed.zip")

# The generic form also reads CSV/XLSX and rewrites one NeTEx profile as another
convert("tables.xlsx", "feed.zip")
convert("epip.xml", "pt.xml", write_profile="pt")
```

Formats are detected from the source when you do not name them — and a `.zip` is decided
by **opening it and looking at its members**, since GTFS and NeTEx both ship inside one.
Pass `source_format=` / `target_format=` to name them outright.

What was lost travels in the return value rather than only in the log:

```python
result = gtfs_to_netex("feed.zip", profile="pt")

result.imported            # {'line': 71, 'service_journey': 10238, ...}
result.not_imported        # what the source held and the model could not read
result.not_representable   # what the model held and the profile cannot say
result.has_losses          # False when both are empty
result.db                  # None unless db= asked for a file
```

Pass `db="city.db"` to keep the intermediate database instead of discarding it — the
conversion then costs you nothing extra if you also want to validate or query the feed.

### Importing from CSV or Excel

```python
from pid_transit import TransitDataset, SpreadsheetImporter

dataset = TransitDataset("my_city.db")

# Import from a single Excel workbook (each sheet is a table)
xlsx_importer = SpreadsheetImporter(format="xlsx")
dataset.import_data(xlsx_importer, "transit_tables.xlsx")

# Or import from a folder of CSV files
csv_importer = SpreadsheetImporter(format="csv")
dataset.import_data(csv_importer, "/path/to/csv/folder/")
```

## Analytics

### Operational Statistics

```python
from pid_transit import TransitDataset, TransitStatistics

dataset = TransitDataset("my_city.db")
stats = TransitStatistics(dataset)

# High-level summary
print(stats.summary())
# {'operators': 1, 'lines': 71, 'stops': 2550, 'service_journeys': 12722,
#  'shapes': 350, 'frequencies': 0, 'transfers': 120, ...}

# Service span (first/last departure per line per day type)
spans = stats.service_span(line_id="L_10")

# Headway analysis
hw = stats.headways("L_10", "WEEKDAY")
print(f"Average: {hw['avg']:.0f}s, Peak: {hw['peak_avg']:.0f}s")

# Total vehicle-hours
vh = stats.vehicle_hours(day_type_id="WEEKDAY")

# Departures per stop
coverage = stats.stop_coverage()

# Journeys and lines per day type
balance = stats.service_balance()
```

You can customize peak hours for headway analysis:

```python
stats = TransitStatistics(dataset, peak_hours=[
    ("07:00:00", "09:30:00"),
    ("17:00:00", "19:30:00"),
])
```

### Feed Comparison

```python
from pid_transit import TransitDataset, FeedDiffer

base = TransitDataset("january_feed.db")
target = TransitDataset("february_feed.db")

report = FeedDiffer(base, target).diff()

# Structured output
print(report.to_dict())
# {'entities': {'line': {'added': 2, 'removed': 0, 'modified': 5}, ...},
#  'service_span_changes': {'L_10/WEEKDAY': '06:00:00-23:00:00 -> 05:30:00-23:30:00'}}

# Markdown report
print(report.to_markdown())
```

The differ compares all 20 entity types (12 ID-keyed tables and 8 composite-key tables) and detects service span changes by leveraging the statistics module.

### Multi-Feed Merge

Combine feeds from different operators into a single dataset, with automatic ID namespacing and optional stop deduplication by proximity:

```python
from pid_transit import TransitDataset, FeedMerger

target = TransitDataset("regional.db")
merger = FeedMerger(target)

stcp = TransitDataset("stcp.db")
metro = TransitDataset("metro.db")

merger.merge(stcp, "stcp")                    # IDs become "stcp:L1", "stcp:S1", etc.
merger.merge(metro, "metro",
             merge_stops_threshold_m=50)       # Merge nearby stops instead of duplicating
```

The merger processes tables in FK-safe order, remaps all foreign key references, and supports a custom namespace separator:

```python
merger = FeedMerger(target, namespace_separator="/")
merger.merge(source, "feed_a")  # IDs become "feed_a/L1", "feed_a/S1", etc.
```

### GTFS-RT Readiness Check

Verify that your dataset is ready for real-time feeds:

```python
from pid_transit import TransitDataset, check_rt_readiness

dataset = TransitDataset("my_city.db")
report = check_rt_readiness(dataset)

print(report.to_markdown())
# Checks: ID uniqueness, URL-safe characters, consistent stop sequences, non-empty IDs
```

The report provides both structured (`to_dict()`) and human-readable (`to_markdown()`) output, listing all passed checks and any issues found with their category, entity type, and ID.

### Matching a Live GTFS-Realtime Feed

Readiness checks the static side; matching checks the live one. The most common failure in
production is a real-time feed referencing `trip_id`s the static feed no longer contains —
this reports that as a resolution rate rather than a silent gap. Requires the `rt` extra.

```python
from pid_transit import TransitDataset, fetch_snapshot, match_snapshot

dataset = TransitDataset("my_city.db")

# Accepts a URL or a local .pb path; `headers` carries the API key many feeds require
snapshot = fetch_snapshot(
    "https://example.org/gtfs-rt.pb",
    headers={"X-Access-Token": "..."},
    timeout=30.0,
)

report = match_snapshot(snapshot, dataset)
print(f"{report.match_rate:.1%} of RT trips resolve against the static feed")
print(report.delay_summary())
# {'count': 812, 'avg_secs': 84.2, 'min_secs': -120, 'max_secs': 940}
print(report.to_markdown())
```

`parse_feed(data: bytes)` parses an already-fetched payload, for when you manage the HTTP
call yourself. `RtSnapshot` and its entities (`RtTripUpdate`, `RtVehiclePosition`,
`RtAlert`) are ordinary Pydantic models, so a snapshot can be persisted, serialized, or
diffed like any other object.

### Frequency Expansion & Compression

Frequency-based feeds represent many departures with one template journey plus a
`Frequency` window, which makes vehicle-hours and headway metrics undercount. Two
inverse dataset operations bridge the representations:

```python
dataset.expand_frequencies()
# {'frequencies_expanded': 12, 'journeys_created': 480, 'passing_times_created': 3360}

dataset.compress_to_frequencies(min_run=3)
# {'runs_compressed': 12, 'journeys_removed': 468, 'frequencies_created': 12}
```

`expand_frequencies()` materializes each window into concrete `ServiceJourney`s at
`start, start+headway, … < end`, shifting the template's passing times — after
which every operational statistic is computed over real departures.
`compress_to_frequencies()` is the inverse: within each `(journey pattern, day
type, timing profile)` class it finds maximal arithmetic departure runs and
collapses each into a template plus a `Frequency`. `expand ∘ compress` reproduces
the original departures exactly (lossless for `exact_times=1`).

### Incremental Updates

Update individual entities without rewriting the full record:

```python
dataset.operators.update("OP_001", {"name": "New Agency Name"})
dataset.lines.update("L_10", {"color": "00FF00"})
```

Updates are validated through Pydantic before writing, so invalid values (e.g., `lat: 999`) will raise a `ValidationError`.

## Data Validation

Run logical consistency checks on your dataset:

```python
report = dataset.validate()

if report.is_valid:
    print("No issues found")
else:
    for issue in report.issues:
        print(f"[{issue.entity_type}] {issue.entity_id}: {issue.message}")
```

The validator runs 20 rules. **V01–V15** cover structural integrity: every `ServiceJourney`
has at least one `PassingTime`, no duplicate passing-time order, arrival times non-decreasing
along a journey, every `Line` has a `JourneyPattern` and at least one `ServiceJourney`,
foreign keys resolve (stops, day types, journey patterns, pathways, frequencies), `DayType`
date ranges are ordered, coordinates are plausible, and the feed's date coverage is sane.

**V16–V20** add semantic checks that structural validation cannot catch:

| Rule | Check |
|---|---|
| V16 | Implied travel speed between consecutive stops exceeds a per-mode ceiling (Haversine) |
| V17 | Journey pattern and passing-time sequences disagree |
| V18 | Two stops share a normalized name within 10 m of each other |
| V19 | Orphan inventory — entities that nothing references |
| V20 | Frequency window sanity (headway sign, start/end ordering) |

Filter by severity to keep a CI gate tight without drowning in advisories:

```python
from pid_transit.core.validator import Severity

report = dataset.validate(min_severity=Severity.ERROR)
```

## Command-Line Interface

PID-Transit includes a CLI tool for common operations:

```bash
# Import a GTFS feed
pid-transit import feed.zip --format gtfs --db city.db

# Import a NeTEx file
pid-transit import netex.xml --format netex --db city.db --timezone Europe/Lisbon

# Import from CSV or Excel
pid-transit import data.xlsx --format xlsx --db city.db
pid-transit import csv_folder/ --format csv --db city.db

# Export to GTFS (shapes.txt is included by default)
pid-transit export --db city.db --format gtfs --output export.zip
pid-transit export --db city.db --format gtfs --output export.zip --no-shapes

# Export to NeTEx (with deduplication, optional gzip)
pid-transit export --db city.db --format netex --output export.xml
pid-transit export --db city.db --format netex --output export.xml.gz --compress

# Disable journey pattern deduplication
pid-transit export --db city.db --format netex --output export.xml --no-dedup

# Convert a feed straight into another format (no database left behind)
pid-transit convert feed.zip --to netex                  # writes feed.netex.xml
pid-transit convert netex.xml -o feed.zip                # format read from the name
pid-transit convert feed.zip -o carris.xml --profile pt  # Portuguese profile
pid-transit convert feed.zip -o out.xml.gz               # .gz implies --compress
pid-transit convert feed.zip --to netex --db city.db     # keep the database too

# Expand frequency windows into concrete journeys (or compress them back)
pid-transit frequencies city.db --expand
pid-transit frequencies city.db --compress --min-run 3

# Normalize journey patterns (deduplicate identical stop sequences)
pid-transit normalize city.db --patterns

# Merge feeds from several operators into one database
pid-transit merge regional.db --source stcp.db --namespace stcp \
                              --source metro.db --namespace metro \
                              --dedupe-stops --distance 50

# Run logical validation (exit non-zero on errors — use this as a CI gate)
pid-transit validate city.db
pid-transit validate city.db --min-severity error --json

# Show dataset statistics
pid-transit stats city.db
pid-transit stats city.db --line L_10 --day-type WEEKDAY

# Compare two feeds
pid-transit diff january.db february.db
pid-transit diff january.db february.db --output report.md
pid-transit diff january.db february.db --output report.json

# GTFS-Realtime: static readiness, live snapshot, and static-vs-RT matching
pid-transit rt-readiness city.db --json
pid-transit rt-snapshot https://example.org/gtfs-rt.pb --db city.db --min-match-rate 0.95
pid-transit rt-check feed.zip --format gtfs --output rt_report.md
```

## Data Model

PID-Transit implements the following Transmodel entities:

| Entity | GTFS Equivalent | Description |
|---|---|---|
| `Operator` | `agency` | Transit operator/agency |
| `Line` | `route` | A public transport line |
| `ScheduledStopPoint` | `stop` | A stop location with coordinates and wheelchair boarding info |
| `StopArea` | `stops` (station) | A grouping of stop points — station, entrance, or generic node |
| `Level` | `levels` | A floor within a station, for multi-level pathfinding |
| `Pathway` | `pathways` | A walkable link between stop points, entrances, and levels |
| `DayType` | `calendar` | Weekly service pattern with validity period |
| `OperatingDayException` | `calendar_dates` | Date-specific service additions/removals |
| `JourneyPattern` | *(derived from trip)* | Ordered sequence of stops for a route variant |
| `PointInJourneyPattern` | *(derived from stop_times)* | A stop within a journey pattern |
| `ServiceJourney` | `trip` | A specific run of a vehicle (with accessibility fields) |
| `PassingTime` | `stop_times` | Arrival/departure times at each stop |
| `FeedInfo` | `feed_info` | Feed-level metadata (publisher, language, validity) |
| `ShapePoint` | `shapes` | Route geometry as ordered lat/lon points |
| `Frequency` | `frequencies` | Headway-based service definition |
| `Transfer` | `transfers` | Stop-to-stop transfer rules and minimum times |
| `FareAttribute` | `fare_attributes` | Fare price, currency, and transfer allowance |
| `FareRule` | `fare_rules` | Which fare applies to which route, zone, or origin/destination |
| `Translation` | `translations` | Per-field translations for any table |
| `Attribution` | `attributions` | Data provenance and producer credits |

### Transport Modes

```python
from pid_transit import TransportMode

# Available modes: BUS, TRAM, RAIL, METRO, FERRY, CABLE_CAR, GONDOLA,
#                  FUNICULAR, TROLLEYBUS, MONORAIL, OTHER
```

### Direction Types

```python
from pid_transit import DirectionType

# Available directions: OUTBOUND, INBOUND, CLOCKWISE, ANTICLOCKWISE
```

## Exception Handling

```python
from pid_transit.core.exceptions import (
    ImportFailedError,
    EntityNotFoundError,
    ValidationError,
)

try:
    dataset.import_data(importer, "bad_feed.zip")
except ImportFailedError as e:
    print(f"Could not load the feed: {e}")

try:
    line = dataset.lines.get_by_id("NONEXISTENT")
except EntityNotFoundError as e:
    print(f"Not found: {e}")
```

## Architecture

```
pid_transit/
  __init__.py              # Public API exports
  cli.py                   # Command-line interface
  core/
    database.py            # SQLite wrapper (TransmodelDatabase)
    schemas.py             # Pydantic models for all 20 entities
    repositories.py        # Repository pattern with CRUD + queries
    dataset.py             # TransitDataset facade
    validator.py           # 20 structural + semantic consistency rules
    geo.py                 # Haversine and the spatial StopGrid index
    patterns.py            # Journey pattern normalization/deduplication
    frequencies.py         # Frequency expansion and compression
    exceptions.py          # Custom exception types
  adapters/
    gtfs_importer.py       # GTFS ZIP -> database
    gtfs_exporter.py       # Database -> GTFS ZIP
    netex_importer.py      # NeTEx XML -> database (streaming, two-pass)
    netex_exporter.py      # Database -> NeTEx XML
    netex_profiles.py      # NeTEx dialects and structural profile detection
    gtfs_rt.py             # GTFS-Realtime fetch, parse, and static matching
    spreadsheet_importer.py # Excel/CSV -> database
  analytics/
    statistics.py          # Operational metrics (headways, vehicle-hours, etc.)
    diff.py                # Feed comparison
    merge.py               # Multi-feed merge with ID namespacing
    rt_readiness.py        # GTFS-RT readiness checker
```

## Creating Custom Adapters

If your company uses a proprietary format or API, you can create your own adapter. Simply query your API, instantiate the PID-Transit Pydantic models, and save them using the repository methods:

```python
# Custom importer example
for route in my_api.get_routes():
    dataset.lines.add(Line(
        id=route.code,
        operator_id="MY_OP",
        name=route.name,
        transport_mode=TransportMode.BUS,
    ))
```

For full importer adapters, implement `import_to_db(db, source)` returning a `dict[str, int]` of record counts. For exporters, implement `export_from_db(db, target)`. Adapter conveniences worth reusing: wrap the import in `db.bulk_session()` (one pinned connection + bulk pragmas), pass `validate=False` to `db.upsert()` for rows your adapter built itself, and expose `on_progress`/`cancel_event` constructor arguments like the built-in importers do.

## Testing

```bash
pip install pid-transit[dev]
pytest
```

The test suite includes 330 tests covering all modules: database CRUD, schema validation, GTFS/NeTEx import/export round-trips, NeTEx profile detection, repository operations, statistics, feed diff, multi-feed merge, GTFS-RT parsing and readiness, frequency expansion, and CLI commands. `tests/test_roundtrip.py` specifically guards format fidelity in both directions. Without the optional `rt` extra, 312 run and the 18 GTFS-Realtime tests skip.

## License

MIT
