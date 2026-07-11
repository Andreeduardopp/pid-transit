# Task 8 — Future Works

**Type:** Research / new scope · **Effort:** Large (each item is independently sized)

Five directions that extend the library rather than harden it. Each is roughly
thesis-chapter-sized and independent of the others; they are ordered by expected
industry demand.

---

## 8.1 GTFS-Fares v2 + NeTEx FareFrame

**Gap.** Fares are the weakest corner of the model today: `FareAttribute` /
`FareRule` implement GTFS Fares **v1** only, have **no NeTEx mapping** at all
(blank cells in the architecture entity map), and their zone references dangle
until [Task 1](01-lossless-field-gaps.md) adds `zone_id` to stops.

**Proposal.** Adopt the GTFS **Fares v2** vocabulary as the internal fare model —
`FareProduct`, `FareLegRule`, `FareMedium`, `RiderCategory`, `FareTransferRule`,
`Area`/`StopArea` membership. Fares v2 maps far better onto NeTEx's fare model
(`FareFrame`: `TariffZone`, `FareProduct`, `DistanceMatrixElement`,
`SalesOfferPackage`) than v1 ever did; v1 becomes an import-side compatibility
path translated into v2 entities.

**Deliverables.** New schema entities + tables; GTFS v1/v2 import, v2 export;
minimal NeTEx `FareFrame` export (zones + flat/zone-based products); entity-map
rows in `architecture.md`.

---

## 8.2 NeTEx profile-aware export & validation

**Gap.** The importer already handles SKI-profile quirks (inline `calls`,
`AvailabilityCondition` bitmasks), but the exporter emits one house dialect and
nothing validates output against any published profile.

**Proposal.** A declared target profile on export —
`NetexExporter(profile="epip")` — where **EPIP** (the EU-mandated European
Passenger Information Profile, per delegated regulation 2017/1926) is the
flagship: required frames, mandatory attributes, id-structure conventions,
`ValidBetween` rules. Plus a `pid-transit netex-check feed.xml --profile epip`
command validating any NeTEx file against profile constraints (XSD subset +
Schematron-style business rules).

**Value.** "EPIP-conformant export" is a concrete, externally verifiable claim —
the difference between "writes NeTEx" and "writes NeTEx that national access
points accept."

---

## 8.3 GTFS-Realtime integration

**Gap.** `analytics/rt_readiness.py` checks whether the static dataset *could*
support an RT feed; nothing consumes one.

**Proposal.** An `analytics/realtime.py` module that ingests GTFS-RT protobuf
(`TripUpdate`, `VehiclePosition`, `Alert`), matches entities to `ServiceJourney`s
/ `ScheduledStopPoint`s (the id-hygiene rules `rt_readiness` enforces are exactly
the matching preconditions), and computes **realized-vs-scheduled** metrics
building directly on formalization §3: realized headways vs. `H_k`, delay
distributions per line/stop/hour, headway regularity (coefficient of variation —
the standard bunching indicator).

**Dependencies.** `gtfs-realtime-bindings` (protobuf) as an optional extra.
Turns the library from static-data tooling into an operations-analysis tool.

---

## 8.4 Frequency expansion & compression — ✅ *implemented*

Shipped as `TransitDataset.expand_frequencies()` /
`compress_to_frequencies(min_run=3)` (core logic in `core/frequencies.py`), the
`pid-transit frequencies <db> --expand|--compress` CLI command, and tests in
`tests/test_frequencies.py` (incl. the `expand ∘ compress` round-trip that
preserves the departure set and passing times exactly for `exact_times=1`). Also
fixed a latent ambiguous-column bug in `statistics.vehicle_hours(day_type_id=…)`
surfaced by the new metric-correctness test. `compress_to_frequencies` groups by
`(journey_pattern, day_type, θ)` today; stacking it on Task 6 Part B's stored
timing profiles remains a future refinement.

**Gap.** Statistics treat `Frequency` rows as opaque: a frequency-based feed
(`frequencies.txt`, or NeTEx `TemplateServiceJourney`) undercounts vehicle-hours
and reports wrong headways, because one template journey stands for dozens of
departures.

**Proposal.** A bidirectional dataset operation:

- **`expand_frequencies()`** — materialize each `Frequency` window into concrete
  `ServiceJourney`s (departures at `start`, `start+h`, … `< end`, passing times
  shifted accordingly; honor `exact_times`). Makes every §3 metric correct on
  frequency-based feeds.
- **`compress_to_frequencies()`** — the inverse detection: within a
  `(pattern, timing-profile, day type)` group (natural fit on top of
  [Task 6](06-pattern-normalization.md) Part B), find arithmetic departure
  progressions and replace them with `Frequency` rows. Formally: detect maximal
  arithmetic subsequences in the sorted departure vector.

Both must round-trip under the [Task 2](02-roundtrip-property-tests.md) harness
(`expand ∘ compress` preserves the departure set exactly when `exact_times=1`).

---

## 8.5 Interchange & transfer-quality analytics

**Gap.** `Transfer`, `Pathway`, and `Level` are modeled and round-trip, but
nothing *analyzes* them — and transfer quality is something no mainstream GTFS
tool reports well.

**Proposal.** An `analytics/interchange.py` module scoring connection quality at
transfer points:

- **Timed-connection audit**: for each `Transfer` (or stop pair within walking
  distance), compare actual arrival→departure gaps against
  `min_transfer_time` / pathway `traversal_time`: flag *missed-by-a-minute*
  connections (gap barely below the walk time) and *long waits* (gap ≫ walk
  time) per day type and hour.
- **Wait-time model**: expected transfer wait onto line ℓ ≈ `H̄_ℓ / 2` for random
  arrival vs. actual scheduled gap for timed arrivals — quantifies how well the
  timetable is synchronized at each interchange (extends §3.1 naturally).
- **Accessibility-aware paths**: with `Pathway` + `Level` + wheelchair fields,
  report interchanges that have no step-free path between the platforms a
  transfer connects.

**Deliverables.** Report API + CLI (`pid-transit interchange city.db --stop X`),
new formalization section (transfer-wait equations), fixtures with pathways.

---

## Suggested sequencing

8.1 and 8.2 are standards-alignment work with direct industry demand; do one of
them first depending on audience (GTFS-centric agencies → 8.1; EU/NAP context →
8.2). 8.3–8.5 build on the analytics layer and benefit from
[Task 4](04-sql-analytics.md) landing beforehand.
