# Task 6 — Pattern Normalization & Timing Patterns

**Type:** Modeling · **Effort:** Medium (part A) + Large (part B) · **Schema impact:** part B adds tables

## Background

Journey-pattern deduplication — the quotient `JP/∼` under
`κ(jp) = (line, direction, σ(jp))` from [`formalization.md`](../formalization.md)
§2 — currently lives **only inside the NeTEx exporter** (`deduplicate_patterns`
flag). The database itself keeps every synthetic `JP_{trip_id}` pattern
(e.g. 12 722 patterns where 220 classes exist).

## Part A — `normalize_patterns()` as a dataset operation

Expose the §2 quotient as an explicit, opt-in transform on the database itself:

```python
result = dataset.normalize_patterns()   # → {"patterns_before": 12722, "patterns_after": 220, ...}
```

```bash
pid-transit normalize city.db --patterns
```

- Compute `κ` per pattern, pick the canonical representative per class (first
  encountered, as the exporter does), remap `service_journey.journey_pattern_id`,
  delete non-canonical patterns and their `point_in_journey_pattern` rows.
- Require / re-verify Task 5's **V16** (journeys actually match their pattern)
  before remapping — normalization is only safe under that invariant.
- Import stays lossless; normalization is explicit and user-triggered, preserving
  the architecture's "import exactly, compact deliberately" stance.

**Why it's worth it:** GTFS→GTFS pipelines and analytics benefit, not just NeTEx
export. Pattern counts become meaningful ("this line has 4 real variants"),
per-pattern statistics become possible, and the NeTEx exporter simplifies to a
pure serializer for pre-normalized databases.

## Part B — Timing patterns (`TimeDemandType`) — future work

Extend the equivalence idea from *stop sequences* to *relative timing profiles*.
Define a journey's timing profile as its offsets from departure:

```
θ(j) = ⟨ τ(t_k) − τ(t_1) : k = 1…n ⟩
```

Two journeys with the same pattern and identical `θ` differ **only in start
time** — which is exactly NeTEx's **`TimeDemandType`** concept (run/wait times
shared across journeys, EN 12896). This is a second, finer quotient stacked on
§2, and a natural new section for the formalization doc.

Scope:

- New entities: `TimeDemandType` (id, journey_pattern_id) + per-stop run/wait
  rows; `service_journey` gains an optional `time_demand_type_id`.
- Importer-side detection: group journeys by `(journey_pattern_id, θ)`; store one
  profile per class. On typical urban feeds this compresses the timetable
  dramatically (thousands of journeys, a handful of profiles per pattern —
  peak/off-peak/night variants).
- NeTEx export emits native `TimeDemandType` elements instead of repeating
  per-journey passing times where the profile is shared; GTFS export re-expands.
  Round-trip losslessness (Task 2 harness) must still hold.
- Analytics dividend: "how many distinct timing profiles does line X run?" is a
  planning-relevant question no GTFS tool answers directly.

## Acceptance criteria

**Part A** — ✅ *implemented* (`dataset.normalize_patterns()`, `pid-transit
normalize`, tests in `tests/test_dataset_api.py` and `tests/test_cli.py`).
- [x] `normalize_patterns()` produces the same pattern set the NeTEx exporter's
      dedup would — verified directly against `build_pattern_dedup_map`'s
      canonical set (`test_normalize_collapses_to_dedup_quotient`).
- [x] Passing times and journey semantics untouched
      (`test_normalize_preserves_journey_semantics`).
- [x] Refuses on pattern violations — raises `ValueError` naming offending
      journeys via the V17 check (`test_normalize_refuses_on_pattern_violation`).
      *(Note: the roadmap text says "V16"; the invariant is V17 in the shipped
      validator numbering — see task 5.)*

**Part B**
- [ ] `θ`-quotient formalized in `formalization.md`.
- [ ] Detection + storage + NeTEx `TimeDemandType` export behind a flag.
- [ ] Round-trip tests pass with the feature on and off.

## Dependencies

Part A depends on Task 5 (V16). Part B depends on Part A and Task 2 (round-trip
harness) being in place.
