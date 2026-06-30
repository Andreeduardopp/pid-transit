# Plan — Path B: make PID-Transit import real European multi-file NeTEx

> Goal: let PID-Transit ingest real-world NeTEx (NeTEx-France / Entur profile, e.g.
> Fluo Grand Est) so we can run a fair NeTEx→GTFS head-to-head **on a competitor's
> native dataset** (complementing the Path-A Porto comparison, which used our own
> export). Success = importing Fluo Riv'Connect with entity counts matching what
> Theoremus produces (63 stops, 97 journeys) and correct passing times.

## 0. Why the current importer fails (empirically established)

`netex_importer.py` is tuned to PID-Transit's own single-file EPIP export. Two hard
stops on Fluo:
1. It does `ET.parse(one_file)` → **rejects the multi-file zip** outright.
2. Pointed at one `Ligne_*.xml` it imports **0 journeys** — it reads `LineRef` as a
   direct child of `ServiceJourney`, but the European profile indirects it:
   `ServiceJourney → JourneyPatternRef → ServiceJourneyPattern → RouteRef → Route → LineRef`.

It also wouldn't find stops (they're `Quay`s in a *separate* file) or calendars
(per-date `DayTypeAssignment`, not weekly flags).

## 1. The Fluo file/structure map (confirmed by inspection)

| File | Frame | Holds | Element notes |
|---|---|---|---|
| `Reseaux.xml` | (Service/General) | **Line[]** defs, Operator, `SiteConnection[]` | Line carries Name, PublicCode, TransportMode, OperatorRef, colour. SiteConnection = transfer (From/To/Distance). |
| `Arrets.xml` | ResourceFrame | **StopPlace[]** → nested **Quay[]** | Quay has `Centroid/Location/Latitude/Longitude` — the physical stop + coords. |
| `Calendriers.xml` | GeneralFrame | **DayType[]** + **DayTypeAssignment[]** | DayType has weekly `DaysOfWeek`; DayTypeAssignment gives **per-date** `<Date>` + `isAvailable` (≈ GTFS calendar_dates). `ValidBetween` for overall range. |
| `Ligne_RIV_N.xml` | ServiceFrame | **Route[]**(→LineRef), **ServiceJourneyPattern[]**(→RouteRef, pointsInSequence), **ScheduledStopPoint[]**, **PassengerStopAssignment[]**(SSP↔Quay), **ServiceJourney[]**(→JourneyPatternRef, dayTypes/DayTypeRef, passingTimes) | passing time = `TimetabledPassingTime` with Arrival/DepartureTime + DayOffset, `StopPointInJourneyPatternRef`. |

## 2. Resolution chains to implement

- **Stop** ← `ScheduledStopPoint`; coords via `PassengerStopAssignment`
  (`ScheduledStopPointRef → QuayRef → Quay.Centroid`). Quays live in `Arrets.xml`, the
  assignment in the `Ligne_*` files → **cross-file**.
- **Line of a journey** ← `ServiceJourney.JourneyPatternRef → ServiceJourneyPattern.RouteRef → Route.LineRef`.
- **JourneyPattern** ← `ServiceJourneyPattern` (line via Route, direction, ordered
  points from `pointsInSequence/StopPointInJourneyPattern/ScheduledStopPointRef`).
- **PassingTime** ← `TimetabledPassingTime`; stop via
  `StopPointInJourneyPatternRef → (SJP points) → ScheduledStopPointRef`.
- **Calendar** ← `DayType` (weekly flags → our DayType) **+** `DayTypeAssignment`
  per-date (→ our `OperatingDayException`, add/remove by `isAvailable`).

## 3. Implementation approach

**Separate code path — do NOT modify the EPIP importer** (it backs the lossless
round-trip and the Path-A head-to-head). Options, recommended first:

1. **New adapter `adapters/netex_eu_importer.py`** implementing the same
   `import_to_db(db, source)` contract, selected by profile **auto-detection**:
   - source is a zip of multiple XML, or the document contains `ServiceJourneyPattern`
     / `PassengerStopAssignment` → European path; else existing EPIP path.
   - CLI: keep `-f netex`; detect internally (or add `--profile eu|epip`).
2. Steps inside the adapter:
   - **Multi-file load**: open the zip, iterate member XMLs; parse each with
     `ET.iterparse` (streaming — also addresses the 1 GB DOM memory note for import)
     and populate cross-file lookup dicts: `quays{}`, `ssp{}`, `ssp_to_quay{}`
     (from PassengerStopAssignment), `routes{}` (→line), `sjp{}` (→route, points),
     `lines{}`, `daytypes{}`, `daytype_dates{}`.
   - **Two-pass**: pass 1 builds the lookups (stops, lines, calendars, patterns);
     pass 2 emits ServiceJourneys + passing times resolving through the maps.
   - Reuse `_reconstruct_time` (DayOffset already handled) and the existing
     `db.upsert` batch path.
   - Namespace: detect per file (Fluo uses the default NeTEx ns).

## 4. Correctness validation (no round-trip here — different profile)

- Import Fluo → assert counts ≈ Theoremus's GTFS: **63 stops, 97 journeys**, 4 lines,
  and a sane passing-time total; run `dataset.validate()` (logical rules) clean.
- Convert imported DB → GTFS via our exporter; **diff against Theoremus's Fluo GTFS**
  (`stops.txt` count, `trips.txt` count, spot-check a trip's `stop_times`).
- Add a fixture-based unit test from a trimmed Fluo subset.

## 5. Benchmark once it works

- **Fluo Riv'Connect** (4 lines): PID-Transit import vs Theoremus (56 ms / 2.8 MB) —
  small, but a real native-competitor dataset both now consume.
- **Larger European dataset for a Porto-scale race**: e.g. Fluo interurbain
  Meuse-55 / Bas-Rhin-67 (`transport.data.gouv.fr`) — gives a second fair head-to-head
  independent of our own export, strengthening §3.4.
- Same harness (`bench.py measure()`), 7+ reps, isolated/foreground.

## 6. Risks, scope, effort

- **NeTEx-in-the-wild variance:** scope explicitly to the **NeTEx-France / Entur**
  profile (covers Fluo + Theoremus's domain). Other producers differ; don't chase
  generality. Edge cases: frequency-based `ServiceJourney`, `DatedServiceJourney`,
  `TimingPoint`s, interchange — out of scope unless present in the target dataset.
- **Calendars** are the fiddliest (per-date assignments × many dates → many
  `OperatingDayException` rows; watch volume on big feeds).
- **Effort:** moderate-to-large but **bounded** — the structure is now fully mapped;
  the work is the two-pass loader + resolution maps (~a focused day). Lower risk than
  it looks because it's isolated in a new adapter.
- **Fallback:** if a given real dataset proves too irregular, the Path-A Porto
  head-to-head (§3.4) already stands as the primary fair comparison; Path B is
  additive evidence + a genuine library capability (reading real European NeTEx).

## 7. Definition of done

- `pid-transit import fluo-riv-netex.zip -f netex --db x.db` yields ≈63 stops /
  ≈97 journeys, validates clean, and round-trips to GTFS matching Theoremus's output;
  EPIP round-trip and all existing tests still pass; a timed Fluo (and one larger
  European) head-to-head is recorded in `RESULTS.md`.
