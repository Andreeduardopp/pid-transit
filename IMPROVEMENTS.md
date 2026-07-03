# PID-Transit — Import/Export Improvement Plan

Findings from a review of the import → export pipeline (2026-07-03), focused on
making NeTEx import lighter on memory/CPU and more fluid for clients such as
the GTFS-NETEX-convertor web app. Ordered by suggested priority.

**Status (2026-07-03): all sections implemented.** Notes on what shipped and
what was learned are inline below. Validated against the Swiss national
timetable (`prod_netex.zip`, 355 files / ~4 GB XML): full import in ~48 min
with 1.25 GB peak RSS producing 1.66 M service journeys, 26.5 M passing times,
67,183 stops (all with coordinates) and 3.7 M calendar exceptions — previously
this feed produced zero passing times. A subset export passes the MobilityData
gtfs-validator with no converter-caused errors.

## 1. Correctness on real-world NeTEx feeds — DONE

### 1.1 `ScheduledStopPointRef` vs `StopPlace` IDs — DONE
Stops are now keyed by **ScheduledStopPoint** id. Coordinates/names resolve
via `PassengerStopAssignment` → `Quay`/`StopPlace` (Quay wins), then the SSP's
own `QuayRef` attribute, then a same-id StopPlace (round-trip case). Feeds
without SSP elements keep the old StopPlace-as-stop behavior.

### 1.2 Cross-file reference resolution (multi-file feeds) — DONE
`NetexImporter.import_to_db` accepts a single file, a directory, a zip
(members streamed, never extracted), or a list of paths. Pass 1 collects
reference data from *all* files before pass 2 streams any ServiceJourneys, so
file order never matters.

### 1.3 Small fidelity bugs — DONE
Stops with missing/invalid coordinates are imported with NULL lat/lon
(schema 3.1: `scheduled_stop_point.lat/lon` are nullable; migration rebuilds
the table for 3.0 databases). The GTFS exporter writes empty values; the
validator reports them as warnings.

### 1.4 Discovered on the real feed (not in the original review)
- Swiss SKI-profile journeys have **no journey patterns**: the stop sequence
  is on the ServiceJourney as `calls/Call` with direct SSP refs. Supported.
- Their calendars are `AvailabilityCondition`s (FromDate + ValidDayBits
  bitmask) referenced from `validityConditions`. Imported as all-false
  day types plus one operating-day exception per set bit.
- `RouteRef` on a pattern names a **Route**, not a Line; resolved through
  `Route.LineRef`. `ServiceJourneyPattern` is treated as `JourneyPattern`.
- Dangling references (lines/stops/day types the feed never defines) are
  skipped/nulled with warnings instead of failing a whole insert batch.

## 2. Memory: stream the XML instead of loading it — DONE

Two-pass `iterparse` with element pruning; rows flush to the DB every 5,000
records. Benchmark (`benchmark/results_import_rework/`, ~150 MB round-trip
file): `netex_import` peak RSS **1,040 MB → 87 MB** (12×) and wall
**13.3 s → 10.2 s** — the §3 DB-write speedups more than pay for parsing
twice. `gtfs_import` also dropped 12.4 s → 7.4 s. Dispatch is a dict lookup on
namespace-qualified tags; the just-ended element is always its parent's last
child, so pruning is O(1) `del parent[-1]`.

## 3. Speed: DB write throughput — DONE

`TransmodelDatabase.upsert` uses one prepared `INSERT OR REPLACE` +
`executemany` per batch, an optional `validate=False` fast path for
adapter-built rows, and `bulk_session()` pins one connection per import run
with `journal_mode=WAL` / `synchronous=NORMAL` on file DBs.
Micro-benchmark: ~75k rows/s (validated, per-call connections) → ~163k rows/s
(unvalidated, bulk session).

## 4. Fluidity for clients: progress + cancellation — DONE

`NetexImporter` / `GtfsImporter` take
`on_progress(entity_type, count, *, file_index, total_files)` (fired per batch
flush with cumulative counts — SSE-friendly) and `cancel_event`
(`threading.Event`; aborts between batches with `ImportCancelledError`,
rolling back the in-flight transaction).

## 5. Coverage symmetry (import ↔ export) — DONE

The original table here claimed the NeTEx **exporter** already wrote these;
it did not — both sides were implemented:

| Data | NeTEx representation (both directions) |
|---|---|
| Calendar validity + exceptions | `ValidBetween` on DayType; `DayTypeAssignment` (+`OperatingPeriod`/`UicOperatingPeriod` and Swiss `AvailabilityCondition` on import) |
| Shapes | `projections/LinkSequenceProjection` + `gml:posList` on the JourneyPattern (shape is per-pattern; per-journey variation within one pattern is not representable) |
| Transfers | `Connection` with From/To SSP refs + `WalkTransferDuration`; `gtfs_transfer_type` in keyList |
| Frequencies | `TemplateServiceJourney` + `HeadwayJourneyGroup`; `gtfs_exact_times` in keyList |
| Wheelchair accessibility | `AccessibilityAssessment`/`MobilityImpairedAccess` on StopPlace, Quay and ServiceJourney |

`bikes_allowed` has no clean NeTEx home and remains GTFS-only.

## Remaining ideas (future work)

- Pass 1 re-parses timetable-only files it takes nothing from; a frame-sniff
  could skip them (don't key off filename conventions).
- Per-TPT `find()` calls dominate pass-2 CPU; a flat spi→stop dict and fewer
  child lookups could shave wall time further.
- The exporter buffers the whole output tree; equivalent streaming on the
  export side remains lower priority.
