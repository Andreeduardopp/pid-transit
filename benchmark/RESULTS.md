# PID-Transit — Comparative Benchmarking (draft data for §4.4)

> Status: PID-transit self-benchmark **complete**. Competitor timed runs **pending a
> decision** (NeTEx profile-compatibility blocker, see §3). Footprint table below uses
> structural facts only; quantitative cells marked *(measure)* still need Docker/build.

## 1. Test environment

| | |
|---|---|
| CPU | Intel Core Ultra series — `Family 6 Model 197` (Arrow Lake), 16 cores |
| RAM | 31.5 GB |
| OS | Windows 11 (26200) |
| Runtime | CPython 3.12.1 |
| Dataset | Porto metropolitan GTFS — 5.07 MB zip; 71 lines, 12 722 service journeys, 2 550 stops, 461 359 passing times |

Methodology: each task run as an isolated subprocess; **T_exec** = wall clock
(mean ± sd over 7 timed reps, 1 warmup discarded); **RAM_peak** = OS peak working
set (`peak_wset`); **Artifact** = output size on disk. `gtfs_import` re-run in
isolation (10 reps) after concurrent-load contamination was detected and discarded.

## 2. PID-Transit measured performance

| Task | T_exec (mean ± sd) | RAM_peak | Artifact |
|---|---|---|---|
| GTFS import (GTFS → SQLite) | **9.04 ± 0.13 s** | 403 MB | 84.7 MB DB |
| NeTEx export (SQLite → NeTEx, dedup on) | **18.3 ± 0.4 s** | **770 MB** | 153.5 MB XML |
| GTFS export (SQLite → GTFS) | **3.97 ± 0.16 s** | 425 MB | 4.6 MB zip |
| NeTEx import (NeTEx → SQLite, round-trip) | **13.33 ± 0.33 s** | 1 040 MB | 42.7 MB DB |

> **NeTEx export optimisation (applied).** Original: 53.9 s / 2 872 MB. The exporter
> serialized via `minidom` (`ET.tostring → minidom.parseString → toprettyxml`), holding
> an ElementTree, a byte string, a second heavier minidom DOM and the pretty string
> at once. Replaced with stdlib `ET.indent` + streaming `ElementTree.write`:
> **18.3 s / 770 MB — 2.9× faster, 3.7× less memory**, round-trip still lossless
> (passing_time 461 359 preserved). NeTEx *import* (1 040 MB, full `ET.parse` DOM of the
> 153 MB file) is now the memory peak — candidate for `iterparse` streaming next.

Notes for the paper:
- **Correct the RAM claim.** The "~85 MB" in the draft is the on-disk SQLite file,
  *not* memory. True peak RSS is 403 MB (GTFS import), 770 MB (NeTEx export, post-fix)
  and 1 040 MB (NeTEx import). Report DB-size and RAM_peak as separate quantities.
- Cold vs warm: first cold-cache import ≈ 19 s; warm steady-state 9.04 s. The draft's
  16.26 s sits between — state cache conditions explicitly.
- NeTEx export is the memory hot-spot (builds the full XML tree in memory): a genuine
  trade-off vs streaming converters, worth discussing honestly.
- Round-trip is lossless on entities (passing_time 461 359 matches exactly); the
  220 vs 12 722 journey_pattern difference is intended deduplication. (The NeTEx
  importer was fixed from an O(SJ×JP) quadratic: >660 s → 13 s.)

## 3. Competitor timed head-to-head — feasibility

| Tool | Lang | Form | Input it expects | Build needs | Verdict |
|---|---|---|---|---|---|
| **Entur** `netex-gtfs-converter-java` | Java | **Library** (Maven Central `org.entur:netex-gtfs-converter-java` 3.0.2) | Nordic profile; **two** datasets (separate stops/quays + timetable), zipped; codespace | Maven + custom Java `main` (Java 26 present) | Hard — incompatible with our single-file EPIP export |
| **Theoremus** `netex-gtfs-converter` | Go | **CLI** | European profile; NeTEx **zip**; `--codespace` | Install Go ≥1.21, `make build` | Most viable — but still needs Entur-style zip input |
| Chouette legacy | Java EE | Service | — | WildFly + PostgreSQL (Docker) | Footprint-only (no Docker here) |
| chouette-core | Ruby/Rails | Service | — | Rails + PostGIS + Redis + delayed_job worker | Footprint-only (no Docker here) |

**Blocker:** both converters expect Entur/Nordic-style NeTEx (zipped, codespace,
split stops-vs-timetable frames). PID-transit emits a single EPIP-style XML, so a
fair timed comparison *on the Porto dataset* requires either (a) an official
Nordic/European NeTEx dataset as common input — run through Theoremus and through
PID-transit's NeTEx import — reported as a separate benchmark; or (b) attempting our
Porto NeTEx through Theoremus and reporting if/where it fails.

### 3.1 Theoremus on the Porto NeTEx — empirical attempt (option b)

Built Theoremus from source (Go 1.26, MIT license; 4.8 MB single binary). Fed it our
own Porto NeTEx export, zipped (`-codespace PT -netex porto_netex.zip`).

**It ran without error but produced an incomplete GTFS.** Self-reported: 145.9 MB
read, 71 lines, 12 722 journeys, **0 quays/stops**, 5.9 s, 232 MB. Output zip = 66 KB
with **only** `agency, routes, trips, calendar, calendar_dates, feed_info` — i.e.
**no `stops.txt`, no `stop_times.txt`, no `shapes.txt`.** The entire schedule (the
461 359 stop times / 22 MB core of the feed) is absent.

Cause: PID-transit serialises stops as Transmodel `ScheduledStopPoint`/`StopArea`;
Theoremus's loader looks for Nordic `StopPlace`/`Quay`, finds none, and silently
drops every stop and passing time.

**Conclusion — not a valid equal-work timing comparison.** Theoremus's 5.9 s did not
include stop/stop_time conversion (the heaviest stage), so its speed/memory cannot be
compared against PID-transit's full NeTEx→GTFS (import 13.3 s + GTFS export 4.0 s,
producing a complete 22 MB `stop_times.txt`).

### 3.2 Common real dataset (option a) — Fluo Grand Est "Riv'Connect"

Real European-profile NeTEx from transport.data.gouv.fr (multi-file zip:
`Arrets.xml`, `Calendriers.xml`, `Ligne_RIV_1..4.xml`, `Reseaux.xml`; 4 lines, 63
stops, 97 journeys; 0.75 MB).

- **Theoremus:** full, correct conversion — 63 stops, 97 journeys, complete
  `stops.txt` + `stop_times.txt`; 56 ms, 2.8 MB. Confirms the Porto failure was
  purely the profile, not a Theoremus defect.
- **PID-transit:** **cannot ingest it.** (i) The importer does `ET.parse` on a single
  file and rejects the multi-file zip outright. (ii) Pointed at one `Ligne_*.xml`, it
  imports **zero** journeys — every `ServiceJourney` is skipped because the French
  profile indirects `LineRef` via `Route`/`JourneyPattern` instead of as a direct
  child (our importer only reads the latter).

### 3.3 The actual comparison result

There is **no real-world NeTEx dataset that both tools fully consume**: Theoremus
reads the Nordic/European profiles but not PID-transit's single-file EPIP dialect;
PID-transit reads its own EPIP dialect but not multi-file European NeTEx. So a fair
*shared-input* timed race is not currently achievable. On **native** input both are
fast (Theoremus 56 ms on Fluo; PID-transit 9–18 s on the 18×-larger Porto feed). The
defensible competitor contribution is therefore **(a) profile interoperability** —
empirically, each tool is confined to its own profile family — **(b) deployment
footprint** (§4), and **(c) the persistent-DB vs. file→file architectural
distinction**, not a single head-to-head wall-clock number.

Enabling a true shared-input race would require development on PID-transit:
multi-file zip import + indirected-`LineRef` resolution + French/European calendar
structures (to *read* Fluo), or `StopPlace`/`Quay` + stop-assignment emission (so
Theoremus can fully *read our* Porto export).

### 3.4 Fair head-to-head achieved (Path A done): Theoremus vs PID-transit, Porto

To make our Porto NeTEx fully consumable by the European-profile Theoremus loader,
two **additive** changes were made to the exporter (round-trip stays lossless,
passing_time 461 359 preserved; 12 unit tests pass):
1. emit top-level `<Quay>` elements in the SiteFrame (a Quay only registers in
   Theoremus when it appears top-level — one nested in a `StopPlace` is consumed with
   it and never indexed);
2. add a `QuayRef` attribute to each `ScheduledStopPoint` (Theoremus resolves a
   passing time → stop via `ScheduledStopPointRef → ScheduledStopPoint.QuayRef → Quay`).

Theoremus then converts our Porto NeTEx **completely** (2 550 stops, 31 MB
`stop_times.txt`) — a valid equal-work comparison. Measured with the same harness
(7 reps, 1 warmup, isolated/foreground; Arrow Lake):

| NeTEx → GTFS (Porto: 2 550 stops, 12 722 journeys, 461 359 passing times) | T_exec | RAM_peak |
|---|---|---|
| **Theoremus (Go)** — single step, file → file | **11.98 ± 3.62 s** | **530 MB** |
| PID-transit — NeTEx → SQLite | 14.60 ± 0.30 s | 1 046 MB |
| PID-transit — SQLite → GTFS | 3.92 ± 0.05 s | 423 MB |
| **PID-transit — NeTEx → GTFS (total)** | **18.53 s** | ~1 046 MB |

**Analysis.** The compiled, streaming Go converter is faster (~12 s vs ~18.5 s,
≈1.5×) and lighter (530 MB vs ~1 GB peak) — exactly what one expects from Go +
streaming vs interpreted, single-threaded Python that also fully **parses the 150 MB
XML into a DOM** (the 1 046 MB peak is the `ET.parse` import stage; `iterparse`
streaming is the open optimisation). The result that matters for the paper: despite
those handicaps, PID-transit stays **within ~1.5×** of a purpose-built compiled
converter *while additionally producing a persistent, queryable, validated 43 MB
SQLite database* as a side effect — Theoremus streams file→file and retains nothing.
Theoremus's high variance (sd 3.62 s ≈ 30%) is also notable vs PID-transit's < 2%.
The comparison is therefore not "we lose on speed" but "we pay ~1.5× wall-clock for a
system of record instead of a one-shot conversion, with zero infrastructure."

## 4. Deployment-footprint comparison (structural; no execution needed)

| | PID-transit | Theoremus | Entur (as converter) | chouette-core | Chouette legacy |
|---|---|---|---|---|---|
| Runtime | CPython | none (static binary) | JVM | Ruby + JVM tooling | JVM (WildFly) |
| Background services | **0** | **0** | 0 (library) | PostGIS + Redis + delayed_job worker | PostgreSQL |
| DB server required | no (embedded SQLite) | no | no | **yes** | **yes** |
| 3rd-party deps | **2 declared / 7 closure** | 0 runtime | JVM + jar graph | **138 top-level / 348 closure** (`Gemfile.lock`) | Java EE stack |
| Persistent queryable store | **yes (SQLite)** | no (file→file) | no (file→file) | yes (PostGIS) | yes (Postgres) |
| Container image | *(measure)* python-slim + pure-py | *(measure)* ~static, smallest | *(measure)* JRE-based | Ruby 3.4 base + PostGIS + Redis (not measured — no Docker on bench) | *(measure)* largest |

**Honest framing:** PID-transit's footprint win is decisive against the
DB+app-server tools (Chouette family) and favorable vs Entur-in-practice. Against a
compiled single-binary converter (Theoremus) it does *not* win on raw deployment
size — Theoremus is leaner. PID-transit's distinguishing value there is the
**persistent, queryable relational store** and Python accessibility, not minimal
footprint. The ~85 MB DB / higher RAM is the cost of being a system-of-record rather
than a one-shot file→file converter.

### 4.1 chouette-core — grounded footprint (timed runs deferred; see `CHOUETTE_PLAN.md`)

Per `CHOUETTE_PLAN.md` §6, the timed head-to-head was time-boxed against Docker
availability. **Docker and a Ruby toolchain are not installed on the bench machine**,
so the wall-clock/RAM rows are deferred. The footprint row below is fully grounded — it
comes from a shallow clone of `enroute-mobi/chouette-core` (master), not estimation.

| Fact | Value | Source |
|---|---|---|
| Framework | Ruby on Rails **7.2.3.1** | `Gemfile.lock` |
| Container base | Ruby **3.4** image | `Dockerfile` (`FROM …/enroute-ruby:3.4`) |
| Database | PostgreSQL **with PostGIS** (adapter default `postgis`) | `Gemfile.lock` (`pg` 1.5.9, `activerecord-postgis-adapter` 10.0.1), `config/database.yml` |
| Cache / cable | **Redis** required | `Gemfile.lock` (`redis` 5.4.1), `config/cable.yml` (`REDIS_URL`) |
| Background jobs | **`delayed_job`** 4.2.0 (DB-backed worker, *not* Sidekiq) | `Gemfile.lock` |
| Standing services | **≥3** beyond the app: PostGIS, Redis, delayed_job worker | config + Gemfile |
| 3rd-party deps | **138** top-level (`Gemfile`) / **348** in lock closure | `Gemfile` / `Gemfile.lock` |
| License | **AGPL-3.0** | `LICENSE` |
| GTFS import op | `Import::Gtfs` | `app/models/import/gtfs.rb` |
| NeTEx export op | `Export::NetexGeneric` | `app/models/export/netex_generic.rb` |
| Domain graph for an import | `Organisation → Workgroup → Workbench → Referential` (all present as models) | `app/models/` |

**Corrections to `CHOUETTE_PLAN.md` found during this pass** (apply before any future
bring-up): (1) the license is **AGPL-3.0**, not CeCILL-B as §0 states; (2) there is
**no `docker-compose.yml` in-tree** — only a bare `Dockerfile` — so §2 step 2 resolves
to "author one from scratch", raising bring-up cost; (3) the confirmed export class is
`Export::NetexGeneric`.

**Architectural-parity result (the publishable point, per plan §5):** PID-transit and
chouette-core fill the *same role* — both are persistent systems of record, not
file→file converters — yet PID-transit delivers import + query + export with **0
background services, 1 process, embedded SQLite, 2 declared deps**, whereas
chouette-core requires **PostGIS + Redis + a delayed_job worker + a Rails app, with a
348-gem dependency closure**. Same role, ~order-of-magnitude less infrastructure. That
contrast stands on its own without a Chouette wall-clock number.
