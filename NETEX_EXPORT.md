# How NeTEx export works — and how it drifted out of conformance

On 2026-09-07 the NeTEx exporter was found to be producing XML that failed the CEN
NeTEx 1.3 schema with **2 771 errors**. Fixing those revealed three more defects hidden
underneath, six in total. All are fixed; the export now validates with **0 errors**.

> The "round-trips without loss" this section originally claimed was
> measured on entities, not on fields. `NETEX_EXPORT_AUDITORIA.md`
> (2026-09-10) found six fields leaving the document uncounted, the
> timezone among them, and the losses are now reported field by field.

This document explains how the export is built, what each defect was, and — the part
worth reading if you only read one section — *why a test suite that was passing could
not see any of it.*

---

## 1. The shape of the export

`NetexExporter.export_from_db` (`src/pid_transit/adapters/netex_exporter.py`) turns a
`TransmodelDatabase` into one NeTEx XML document. There is no intermediate model: the
database rows are read table by table and written directly as XML elements.

```
TransmodelDatabase (SQLite)
        │
        ▼
PublicationDelivery
 └── dataObjects
      └── CompositeFrame
           └── frames
                ├── ResourceFrame        operators
                ├── SiteFrame            stop places, each with its quay
                ├── ServiceFrame         lines, stop points, geometry, patterns
                ├── ServiceCalendarFrame day types and their exceptions
                └── TimetableFrame       journeys and passing times
```

Five `_write_*_frame` methods, called in that order, each appending one frame. The whole
tree is built in memory, then `ET.indent` + `ElementTree.write` streams it to disk.

Two options shape the output:

- **`deduplicate_patterns=True`** (the default) collapses journey patterns that share
  `(line, direction, ordered stop sequence)` into one canonical pattern, and remaps every
  journey's `JourneyPatternRef` to it. On the Porto feed this is 12 722 → 220 patterns.
  It is lossy by design at the pattern level and lossless at the journey level.
- **`compress=True`** gzips the output.

`profile` is accepted and stored but **not read anywhere** — the exporter emits one
dialect regardless. Making it real is roadmap task 9, phase 4.

### Where the data comes from

| Frame | Tables read |
|---|---|
| `ResourceFrame` | `operator` |
| `SiteFrame` | `scheduled_stop_point` (one `StopPlace` + one nested `Quay` each) |
| `ServiceFrame` | `line`, `scheduled_stop_point`, `shape_point`, `journey_pattern`, `point_in_journey_pattern`, `transfer` |
| `ServiceCalendarFrame` | `day_type`, `operating_day_exception` |
| `TimetableFrame` | `service_journey`, `passing_time`, `frequency` |

### Two places where GTFS and NeTEx disagree

**Times past midnight.** GTFS writes `25:10:00` for a journey that departs after
midnight; `xs:time` will not accept it. `_normalize_time` splits such a value into a
legal time plus a `DayOffset` sibling (`25:10:00` → `01:10:00` + offset 1). The Porto
feed has 9 486 of these.

**Shape geometry.** The two formats disagree about what geometry even *is*, and the gap
cannot be closed by reformatting. Section 2 works through it with a real example.

---

## 2. Shape geometry, in practice

This is the only place in the exporter where the two formats disagree about the shape of
the world rather than about syntax. It is also defect 3.

### What each side gives you

GTFS puts the whole route's geometry in one flat list, in `shapes.txt`. One polyline per
trip, no relationship to the stops at all — just an ordered bag of points:

```csv
shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence
400_0_1|1,41.14621,-8.57823,1
400_0_1|1,41.14638,-8.57799,2
400_0_1|1,41.14655,-8.57771,3
...                                    (58 rows for this shape)
```

NeTEx has no object for "the line the vehicle drives". It only has `ServiceLink`: the
link **between two consecutive stops**, carrying its own geometry.

```xml
<ServiceLink id="400_0_1|1:link:1" version="1">
  <projections>
    <LinkSequenceProjection id="400_0_1|1:link:1:proj" version="1">
      <gml:LineString gml:id="ls_1">
        <gml:posList>41.14621 -8.57823 41.14638 -8.57799 ...</gml:posList>
      </gml:LineString>
    </LinkSequenceProjection>
  </projections>
  <FromPointRef ref="FRX3" version="1"/>
  <ToPointRef  ref="RSH"  version="1"/>
</ServiceLink>
```

So a GTFS shape does not *become* a NeTEx object. It has to be **cut into as many pieces
as there are hops**, and each piece attached to the pair of stops it runs between. Seven
stops means six links, and the 58 points have to be distributed among them.

### A real pattern, cut

Porto pattern `JP_400_0_1|228|D1|T4|N2`: **7 stops, 58 shape points**. Here is what
`_split_shape_by_stops` actually produces:

| hop | from | to | points |
|---|---|---|---:|
| 1 | FRX3 | RSH | 11 |
| 2 | RSH | SP3 | 14 |
| 3 | SP3 | MERL | 9 |
| 4 | MERL | VML | 8 |
| 5 | VML | LGT1 | 12 |
| 6 | LGT1 | AZV3 | 9 |
| | | **sum** | **63** |

63 points written for a 58-point shape. The extra 5 are the point of the whole design.

### Why 63 and not 58

Each cut happens *at* a shape point, and that point belongs to **both** segments — it is
the last point of one hop and the first point of the next:

```
shape points:  0 ─ 1 ─ 2 ─ … ─ 10 ─ 11 ─ … ─ 23 ─ … ─ 57
                                  │           │
hop 1:         0 ─────────────── 10                        (11 points)
hop 2:                           10 ───────── 23           (14 points)
hop 3:                                        23 ── …      ( 9 points)
```

Segments overlap by exactly one point at every junction. With 6 hops there are 5
junctions, so `63 − 5 = 58` — the original count, exactly.

That overlap is what makes the cut **reversible**. The importer walks the pattern's
`linksInSequence`, concatenates the segments, and drops the first point of every segment
after the first, because it already has it as the last point of the previous one:

```python
coords = list(segments[0])
for seg in segments[1:]:
    coords.extend(seg[1:] if seg[0] == coords[-1] else seg)
```

Without the shared junction, the reassembled line would have gaps at every stop — the
geometry would be quietly wrong in a way no entity count would reveal. Measured at
scale: all **91 850** Porto shape points come back identical.

### How the cut positions are chosen

The stops and the shape points are two independent sequences of coordinates; nothing in
GTFS says which point belongs to which stop. So:

1. **The ends are anchored.** First stop → point 0, last stop → the final point. The
   shape must start where the pattern starts and end where it ends.
2. **Every intermediate stop snaps to its nearest remaining shape point**, searching only
   forward from the previous cut — so the cuts can never go backwards along the line.
3. **Cuts are forced strictly increasing**, and clamped to leave at least one point for
   every hop still to come. Without that, two stops close together could snap to the same
   point and produce a one-point `LineString`, which is not valid geometry and would put
   the export straight back to failing the XSD.
4. **If the shape has fewer points than the pattern has stops**, no split is possible
   without degenerate hops, so the pattern gets **no geometry at all**. Dropping geometry
   is recoverable; emitting invalid geometry is not.

Step 2 is where the approximation lives. Snapping to the nearest point is a guess about
where along the road the bus passes each stop, and a stop 30 m off the line, or a route
that doubles back on itself, can pick a point a little early or a little late. The
polyline is reproduced exactly; **where it is cut** is approximate.

### What the cut costs

Step 2 scans forward from the previous cut to the last index the remaining hops can
spare, so the split is `O(stops x shape_points)` per distinct cut. That bound is close to
the end of the array — it trims about **2%** of the scan, measured — so it exists to stop
a stop being snapped somewhere the following hops need, not to make the split fast. On a
3 000-point shape with 60 stops one cut takes ~26 ms; a feed with a thousand distinct
cuts therefore pays tens of seconds on export.

Making it linear means walking the shape once and taking the first local minimum per
stop, which is a *different* approximation: it diverges from nearest-point snapping
exactly where the shape doubles back, which is where the snapping is already least
reliable. That trade has not been taken, because no feed currently on hand can validate
it — `research/feeds/prod_netex.zip` carries no `ServiceLink` geometry at all (verified:
0 of 353 files contain `<ServiceLink`, `<linksInSequence` or `<gml:posList`), so the only
evidence available is synthetic.

### The alternative that was rejected

One `ServiceLink` per pattern, from the first stop to the last, carrying the whole
58-point polyline. It is far less code, it validates against the XSD, and the round-trip
would be trivially exact.

It was rejected because a `ServiceLink` *means* one hop. Such a document would claim the
network has a single direct link from FRX3 to AZV3 and no way to travel between the five
stops in between — schema-valid NeTEx asserting something false about the network. Any
consumer walking links pairwise would read it wrongly, which is exactly the failure mode
that produced defects 1 and 2.

---

## 3. What was wrong

| # | Defect | Errors | Fix |
|---|---|---:|---|
| 1 | `QuayRef` as an **attribute** on `ScheduledStopPoint` | 2 550 | `PassengerStopAssignment` in the `ServiceFrame` |
| 2 | `quays` as a direct child of `SiteFrame` | 1 | `Quay` nested inside its `StopPlace` |
| 3 | `projections` on `JourneyPattern` | 220 | one `ServiceLink` per hop, named from `linksInSequence` |
| 4 | `connections` written last in the `ServiceFrame` | 1 | must precede `stopAssignments` |
| 5 | `ScheduledHeadwayInterval` before its two bounds; `frequencyGroups` before `passingTimes` | 2 | schema order in both places |
| 6 | `gml:id` derived from the feed's own id | 7 143 | numbered ids |

### 1 and 2 — a valid dialect is not a valid schema

Both were added deliberately, and both were recorded at the time as *additive* changes
that kept the round-trip lossless. The goal was to let the European-profile Theoremus
converter consume our output: it resolves a passing time to a physical stop via
`ScheduledStopPoint → Quay`, and it only indexes a `Quay` that appears top-level.

Both claims were true of the *data* and false of the *schema*. `QuayRef` is an element in
NeTEx, never an attribute, and `ScheduledStopPoint` admits it in neither form. `SiteFrame`
has no top-level `quays` container.

The conformant way to bind a stop point to a quay is a `PassengerStopAssignment` — which
**our own importer already read** and our exporter simply never wrote. The mechanism was
supported on one side of the round-trip and absent on the other, and nothing noticed,
because the round-trip tests only ever fed the importer our own output.

### 3 — a modelling mismatch, not a misplaced tag (see section 2)

`projections` was emitted on the `JourneyPattern`, which reads naturally: a
`JourneyPattern` *is* a kind of `LinkSequence`, and a `LinkSequence` has projections. But
checking the XSD from two independent positions shows `projections` is not among a
`JourneyPattern`'s permitted children at all, and after `pointsInSequence` the schema
expects `linksInSequence`. `Route` does not accept it either. `ServiceLink` is the only
host the schema allows.

So this could not be fixed by moving a line. It required the per-hop split described
above, plus matching importer support to reassemble it.

### 4 and 5 — order is not cosmetic

`ServiceFrame` and `ServiceJourney` are XML **sequences**, not bags. Their children are
valid only in schema order, and the writer had been appending them in whatever order was
convenient:

- `connections` was written last, but must come before `stopAssignments`;
- `ScheduledHeadwayInterval` was written before `FirstDepartureTime`/`LastDepartureTime`,
  which must come first;
- `frequencyGroups` was written before `passingTimes`, which must come first.

These were not fixed by three more hand-placed swaps, because the next insertion would
have repeated the mistake. The writer now declares the schema order once
(`_SERVICE_FRAME_ORDER`, `_JOURNEY_ORDER`) and sorts the children into it before closing
the element. A container added later and left out of that list raises immediately at
export time — a loud failure instead of silently invalid XML.

### 6 — two kinds of identifier that look alike

`gml:id` is `xs:ID`, an XML **NCName**: it must start with a letter or underscore and
contain only letters, digits, `.`, `-`, `_`. A NeTEx object id is a far looser string.
Real feeds exploit the difference — Porto's shape ids contain `|`, as in
`603_0_1|0`.

The exporter derived one from the other, sanitising only `:`. Every remaining illegal
character produced an error, 7 143 of them. Since these ids are internal to the GML and
carry no meaning for a consumer, they are now simply numbered.

---

## 4. Why none of this was caught

This is the part that generalises.

### Each defect hid the next

An invalid child makes the validator **abandon that content model**, so the children
underneath it are never checked. Defect 6 lived inside the `projections` block that
defect 3 caused to be rejected whole — 7 143 errors, completely invisible, until defect 3
was fixed. The error count went *up* twice during the repair, which is the expected shape
of unpeeling this kind of problem, not a sign of regression.

A corollary: "N schema errors" is a lower bound, never a count.

### The reference feed did not exercise the defects

The Porto STCP feed has **no transfers and no frequencies**. Defect 4 lives in the
`connections` block and defect 5 in the frequency block, so neither was ever emitted
during validation. They had likely been latent for months. Any feed carrying transfers or
frequencies would have produced invalid NeTEx.

The two small fixtures used during the repair caught defects 4 and 5 immediately — they
have both — but *missed* defect 6, because their ids are tidy (`SH1`, `T1`) and contain
nothing illegal. It took a real feed's `603_0_1|0` to expose it. Neither a clean synthetic
fixture nor a single real feed is sufficient alone.

### The tests were checking the wrong invariant

The suite verified **round-trip fidelity**: export, re-import, compare entity counts.
That is a real and useful property, and it passed throughout — the data was never
corrupted. But it can only ever prove that *our importer* understands *our exporter*.
Both sides shared the same misunderstanding of the schema, so the round-trip agreed with
itself perfectly while the output was unreadable by anything else.

Schema conformance is a claim about the outside world, and only an outside checker can
support it.

### The claim decayed silently

"NeTEx export passes the CEN 1.3 XSD with 0 errors" was measured in May 2026 and was
true then. Between then and September the export gained shape geometry and the two
interoperability changes, and nobody re-ran the check — it lives in `research/validator/`
outside the library, needs `lxml`, which is not a project dependency, and takes four
minutes. The stored report had been sitting at `SKIPPED: 'lxml' not installed`.

The claim did not become false loudly. It just stopped being re-measured.

---

## 5. Where things stand

- Porto export: **0 schema errors** against CEN NeTEx 1.3.
- Round-trip with de-duplication off: **zero mismatches** across all ten entity types,
  including 91 850 shape points.
- Test suite: 280 → **300**. The new tests target exactly what the reference feeds could
  not reach — frame child order on a feed that *does* carry transfers, `gml:id` validity
  for ids containing `|` and `:`, a shape too short to split (must yield no geometry
  rather than one-point `LineString`s), and exact polyline reconstruction.

**Three defects were found in this work after the fact and are now fixed** — the code
review record is in the session linked below.

1. Links were cut once per *shape*, using whichever pattern reached it first, and every
   other pattern on that shape referenced the whole list. A short-turn variant sharing a
   `shape_id` with the full pattern emitted more links than it had hops, with endpoints
   naming stops it does not serve — `linksInSequence` and `pointsInSequence` contradicting
   each other. Cuts are now keyed by `(shape, stop sequence)`; the first cut of a shape
   keeps the shape's id, later ones are suffixed `:v2`, `:v3`.
2. On import, shape identity was parsed out of the first link's id (`rsplit(":link:")`),
   which only means anything for links this exporter wrote. Two patterns sharing a first
   link collapsed onto one shape and the second silently inherited the first's geometry.
   Identity now comes from the whole link sequence.
3. `on_service_link` read geometry only from `projections/LinkSequenceProjection`, but the
   CEN `LinkGroup` also admits `gml:LineString` directly on the Link, and producers use
   that form. Such feeds imported with every shape silently dropped.

Both silent drop paths — a shape too short to cut, and a pattern with an unreadable link
— now log a warning, matching what every other drop in these two modules already did.

**One consequence is still open.** The Theoremus head-to-head was measured on an export
carrying the `QuayRef` attribute that defect 1 removed. `PassengerStopAssignment` is the
conventional mechanism and a European-profile loader very likely reads it — but that is
not a measurement, and it has not been re-run.

### Reproducing the check

```bash
pip install lxml                                  # not a project dependency
python research/validator/run_validation.py       # ~10 min; the XSD step alone is ~9
```

Writes artefacts and a report to `research/validator/output/`. The long-form analysis
lives in `research/validator/output/validation_analysis.md`; the benchmark record is
`research/benchmark/RESULTS.md` §3.6 (the failure) and §3.7 (the repair).
