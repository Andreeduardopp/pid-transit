# Plan — Benchmarking chouette-core (GTFS import + NeTEx export)

> Companion to `RESULTS.md`. Chouette-core is the closest architectural analogue to
> PID-Transit in the competitor set — both are **persistent systems of record**, not
> file→file converters — so this is the most apt head-to-head in the paper. But it is
> a heavyweight, multi-service Rails app, so the measurement protocol differs from the
> CLI tools and must be normalized carefully.

## 0. What chouette-core actually is (grounded in its `Gemfile.lock`, master)

| Component | Detail |
|---|---|
| App | Ruby on Rails **7.2.3** (Ruby ≥ 3.1; confirm `.ruby-version`) |
| Database | PostgreSQL (`pg` 1.5.9) **with PostGIS** (`activerecord-postgis-adapter` 10.0.1) |
| Background jobs | **`delayed_job`** (`delayed_job_active_record` 4.2.0) — DB-backed worker, *not* Sidekiq |
| Cache | Redis 5.4.1 |
| License | CeCILL-B (open source) |

**Consequence:** a conversion is **not** a subprocess. It is an asynchronous
*Operation*: upload → `Import` operation (a delayed_job processes it into a
`Referential` stored in PostGIS) → `Export` operation (worker serializes NeTEx). We
must drive and time these operations headlessly, not click a web UI.

## 1. Prerequisites

- **Docker Desktop / Engine + Compose** — *currently not installed on the bench
  machine; this is the primary blocker.* Alternative: a Linux VM with native Ruby +
  Postgres.
- Ruby ≥ 3.1 toolchain, PostgreSQL 14+ with PostGIS, Redis (only inside containers).
- Build deps for native gems: `libpq`, `proj`/`geos`/`gdal` (PostGIS adapter),
  `nokogiri` (libxml2). These are the usual Ruby-setup friction points.

## 2. Bring-up steps

1. `git clone https://github.com/enroute-mobi/chouette-core`.
2. Check for an official `docker-compose.yml`. If absent, author one with services:
   `db` (postgis/postgis image), `redis`, `app` (Rails), `worker` (`rake jobs:work`
   / `delayed_job`).
3. `bundle install`; `bin/rails db:create db:schema:load` (or `db:migrate`); enable
   the PostGIS extension (`CREATE EXTENSION postgis;`).
4. Seed the minimal Chouette domain graph the import needs:
   `Organisation → Workgroup → Workbench → Referential`. (This is the fiddly part —
   imports attach to a Workbench; there are factory/seed helpers in `spec/factories`.)
5. Stage the **Porto feed** (`gtfs_feed (1).zip`) as the import source so results are
   comparable to the rest of §4.4.

## 3. The two operations to benchmark

Drive both **inline** via `bin/rails runner` so timing is clean and excludes web/UI
latency (run the operation's `perform` synchronously rather than enqueuing):

- **GTFS import** — instantiate the GTFS import operation against the Workbench
  pointing at the Porto zip; run it; it populates a Referential in PostGIS.
  *(Class names to confirm in-tree, e.g. `Import::Gtfs` / `Workbench::Import`.)*
- **NeTEx export** — instantiate the NeTEx export operation on that Referential; run
  it; capture the produced NeTEx file.

Wrap each `rails runner` invocation with the **same `measure()` harness** used for the
CLI tools (`benchmark/bench.py`), so wall-clock and peak RSS are recorded identically.

## 4. Metrics (same three axes as the rest of §4.4, plus DB-server overhead)

| Metric | How to measure for chouette-core |
|---|---|
| **T_exec** | Wall time of the `rails runner` process (harness `measure()`). Cross-check against `Operation.started_at/ended_at` persisted in the DB. N=7 reps + 1 warmup, mean ± sd. |
| **RAM_peak (worker)** | Peak RSS of the Ruby worker process — the figure directly comparable to PID-Transit's process RSS. |
| **RAM (DB server)** | Peak RSS of the `postgres` container (`docker stats` sampling). Report **separately** — it is the standing cost of the external DB engine that PID-Transit's embedded SQLite avoids. |
| **Artifact** | Size of the exported NeTEx, **and** the on-disk size of the PostGIS dataset (Chouette's analogue of our 84.7 MB SQLite file) — `SELECT pg_database_size(...)`. |
| **Footprint** | Container/service count (≥3: db, redis, worker, app), `docker images` sizes, gem-dependency count. Feeds the §4 footprint table. |

## 5. Fairness normalization (essential for the paper)

- Chouette does **more** than convert: schema validation, PostGIS spatial storage,
  dataset **versioning**, multi-tenant workbenches. So raw T_exec will and *should*
  be higher — frame it as the cost of a full production data-management platform.
- The honest, strong comparison is **architectural parity, resource asymmetry**: both
  PID-Transit and chouette-core are persistent systems of record, but PID-Transit
  delivers import+query+export with **0 background services, 1 process, embedded
  SQLite, 2 deps**, whereas chouette-core needs **PostGIS + Redis + a delayed_job
  worker + a Rails app**. That contrast — same role, ~order-of-magnitude less
  infrastructure — is the result worth publishing, more than the wall-clock delta.

## 6. Risks & fallback

- **Docker not installed** → install Docker Desktop or use a Linux VM (biggest cost).
- **No turnkey compose / native-gem friction** (PostGIS, nokogiri) → budget setup
  time; a half-to-full day is realistic before the first clean run.
- **Domain setup** (Organisation/Workgroup/Workbench/Referential) is non-trivial; the
  import won't run without it.
- **Operation class names / API** must be confirmed in-tree (`app/models`,
  `spec/factories`) — the names above are indicative.
- **Time-box:** if bring-up exceeds ~1 day, fall back to the **footprint-only** row we
  already have (services, deps, DB-server requirement). The interoperability +
  footprint + architectural-parity story stands without a Chouette wall-clock number.

## 7. Effort estimate

Setup dominates (½–1 day, mostly Docker + PostGIS gems + Chouette domain seeding).
Once up, the runs themselves are minutes. Legacy Chouette (Java EE / WildFly) is
**out of scope for timing** — footprint-only — as agreed.
