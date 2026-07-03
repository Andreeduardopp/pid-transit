# PID-Transit

A professional transit data library based on the Transmodel / NeTEx conceptual model. It allows companies to easily create a relational transit database, interact with data using strongly-typed objects (Pydantic), and translate feeds seamlessly between GTFS and NeTEx formats.

## Features

- **Transmodel Core Database**: Underlying SQLite database modeled accurately against European transit standards (Transmodel).
- **Object-Oriented API**: Manage your datasets via the `TransitDataset` facade and clean Repository classes with filtered queries and incremental updates.
- **Strong Typing**: Full Pydantic v2 validation for all 13 entity types (`Operator`, `Line`, `ScheduledStopPoint`, `ServiceJourney`, `ShapePoint`, `Transfer`, etc.).
- **Query & Filtering**: Filtered queries, lookups by ID, field-based search, counting, and ordered results across all repositories.
- **Full GTFS Coverage**: Imports and exports all standard GTFS files including `shapes.txt`, `frequencies.txt`, `transfers.txt`, `feed_info.txt`, and accessibility fields (`wheelchair_boarding`, `wheelchair_accessible`, `bikes_allowed`).
- **NeTEx Round-Trip**: Full import and export of NeTEx XML with XSD-compliant output, journey pattern deduplication, and DayOffset handling for overnight services.
- **Analytics**: Built-in operational statistics (headways, vehicle-hours, service spans, stop coverage), feed comparison/diff, and GTFS-RT readiness checks.
- **Multi-Feed Merge**: Combine feeds from different operators with ID namespacing and Haversine-based stop deduplication.
- **CLI Tool**: Command-line interface for import, export, validate, stats, and diff operations.
- **Spreadsheet Import**: Load transit data directly from Excel workbooks or CSV folders.
- **Data Validation**: Logical consistency checks on the Transmodel database (FK integrity, required fields, passing time order).

## Installation

Once packaged, install via pip:
```bash
pip install pid-transit
```

*Note: Python 3.10+ is required. Dependencies: `pydantic>=2.0`, `openpyxl>=3.0`.*

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

The exporter writes all standard GTFS files: `agency.txt`, `routes.txt`, `stops.txt`, `calendar.txt`, `calendar_dates.txt`, `trips.txt`, `stop_times.txt`, `shapes.txt`, `frequencies.txt`, `transfers.txt`, and `feed_info.txt`. Accessibility fields (`wheelchair_boarding`, `wheelchair_accessible`, `bikes_allowed`) are included automatically when present.

### Exporting to NeTEx

```python
from pid_transit import TransitDataset, NetexExporter

dataset = TransitDataset("my_city.db")
exporter = NetexExporter(
    profile="EPIP",
    deduplicate_patterns=True,  # reduces output size significantly
    compress=False,             # set True for .gz output
)

dataset.export_data(exporter, "output_netex.xml")
```

The `deduplicate_patterns` option groups journey patterns with identical stop sequences (same line, direction, and ordered stops), writing only canonical patterns and remapping references. This can reduce NeTEx output from thousands of duplicate patterns to a few hundred unique ones.

The export includes calendar validity (`ValidBetween`) and exceptions (`DayTypeAssignment`), shapes (`LinkSequenceProjection` with `gml:posList` on the journey pattern), transfers (`Connection`), frequencies (`TemplateServiceJourney`/`HeadwayJourneyGroup`), and wheelchair accessibility (`AccessibilityAssessment`) — all of which the NeTEx importer reads back, so GTFS → NeTEx → GTFS round-trips are lossless for these fields (`bikes_allowed` is GTFS-only).

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

The differ compares all 13 entity types (both ID-based and composite-key tables) and detects service span changes by leveraging the statistics module.

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

Checks include:
- Every `ServiceJourney` has at least one `PassingTime`
- No duplicate passing time order within a journey
- Every `Line` has at least one `JourneyPattern`

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

# Export to GTFS (with shapes)
pid-transit export --db city.db --format gtfs --output export.zip --include-shapes

# Export to NeTEx (with deduplication, optional gzip)
pid-transit export --db city.db --format netex --output export.xml
pid-transit export --db city.db --format netex --output export.xml.gz --compress

# Disable journey pattern deduplication
pid-transit export --db city.db --format netex --output export.xml --no-dedup

# Run logical validation
pid-transit validate city.db

# Show dataset statistics
pid-transit stats city.db
pid-transit stats city.db --line L_10 --day-type WEEKDAY

# Compare two feeds
pid-transit diff january.db february.db
pid-transit diff january.db february.db --output report.md
pid-transit diff january.db february.db --output report.json
```

## Data Model

PID-Transit implements the following Transmodel entities:

| Entity | GTFS Equivalent | Description |
|---|---|---|
| `Operator` | `agency` | Transit operator/agency |
| `Line` | `route` | A public transport line |
| `ScheduledStopPoint` | `stop` | A stop location with coordinates and wheelchair boarding info |
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

### Transport Modes

```python
from pid_transit import TransportMode

# Available modes: BUS, TRAM, RAIL, METRO, FERRY, CABLE_CAR, OTHER
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
    schemas.py             # Pydantic models for all 13 entities
    repositories.py        # Repository pattern with CRUD + queries
    dataset.py             # TransitDataset facade
    validator.py           # Logical consistency checks
    exceptions.py          # Custom exception types
  adapters/
    gtfs_importer.py       # GTFS ZIP -> database
    gtfs_exporter.py       # Database -> GTFS ZIP
    netex_importer.py      # NeTEx XML -> database
    netex_exporter.py      # Database -> NeTEx XML
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

The test suite includes 152 tests covering all modules: database CRUD, schema validation, GTFS/NeTEx import/export round-trips, repository operations, statistics, feed diff, multi-feed merge, GTFS-RT readiness, and CLI commands.

## License

MIT
