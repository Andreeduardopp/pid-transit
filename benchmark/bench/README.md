# chouette-core timed head-to-head — reproduction

Brings up `enroute-mobi/chouette-core` (master) on Docker and times its **GTFS import**
and **NeTEx export** against the same Porto feed used for PID-Transit, under a harness
comparable to `../bench.py`. Results feed `../RESULTS.md` §4.1.

## Why a custom Dockerfile
The upstream `Dockerfile` builds `FROM` a **private** enroute registry image
(`enroute-ruby:3.4`) plus internal `build.sh` tooling, so it cannot be built externally.
`Dockerfile.bench` reproduces the runtime from the public `ruby:3.4` image plus the system
libraries the native gems need (`geos`/`proj`/`gdal` for the PostGIS adapter, `libpq`,
`libxml2`, `cmake` for `rugged`). The engine gems (`gtfs`, `netex`, …) install from public
Bitbucket git sources declared in chouette's `Gemfile`.

## Minimal stack (2 containers)
Per `../CHOUETTE_PLAN.md` §3 the operations run **synchronously inline** via `rails runner`,
so Redis, the delayed_job worker and the web frontend are unnecessary. `docker-compose.yml`
runs only **PostGIS** + the **Ruby app** container.

## Steps
```bash
# 0. clone chouette-core next to this dir and place the Porto feed
git clone --depth 1 https://github.com/enroute-mobi/chouette-core.git ../chouette-core
mkdir -p feed && cp "/path/to/gtfs_feed (1).zip" feed/porto.zip

# 1. build + up (PostGIS becomes healthy, app idles on sleep infinity)
docker compose up -d --build

# 2. install gems (engine gems from public Bitbucket; ~5 min, native builds)
docker compose exec -T app bash -lc 'cd /app && bundle install -j4'

# 3. DB: create shared_extensions schema, then load schema (postgis lands in shared_extensions)
docker compose exec -T app bash -lc 'cd /app && bin/rails db:drop db:create'
for db in chouette2 chouette2-test; do
  docker compose exec -T db psql -U chouette -d "$db" -c \
    "CREATE SCHEMA IF NOT EXISTS shared_extensions; GRANT ALL ON SCHEMA shared_extensions TO PUBLIC;"
done
docker compose exec -T app bash -lc 'cd /app && bin/rails db:schema:load'

# 4. run the timed benchmark (seeds the domain, then N reps of import + export)
python3 chouette_bench.py --reps 3 --warmup 1 --profile european
```

## Files
- `Dockerfile.bench`, `docker-compose.yml` — the 2-container stack.
- `runner/seed.rb`   — seeds Organisation→Workgroup→Workbench with the generic `netex`
  objectid format (the factory default `stif_codifligne` silently drops non-French lines)
  and persists the default providers.
- `runner/import.rb` — purges prior referentials (a re-import of the same feed window is
  rejected as overlapping), then runs `Import::Gtfs#import` and prints `OP_SECONDS` + counts.
- `runner/export.rb` — runs a synchronous `Export::NetexGeneric` (EPIP `european` profile,
  full-referential scope) and copies the artifact to `/out`.
- `chouette_bench.py` — wraps each `rails runner` in GNU `/usr/bin/time -v` (worker max RSS)
  and reads the PostGIS container cgroup `memory.peak`; writes `chouette_results.json`.
- `chouette_results.json` — the recorded run (N=3 + 1 warmup) summarized in `../RESULTS.md` §4.1.

## Result (this run, Linux 16c / 34.6 GB / Docker 29 / Ruby 3.4.9)
| | chouette-core | PID-Transit (same machine, `../results_linux/`) |
|---|---|---|
| GTFS import T_exec | 323.7 ± 6.1 s | 12.76 ± 1.87 s |
| NeTEx export T_exec | 98.0 ± 0.8 s | 10.98 ± 0.08 s |
| worker RSS (import / export) | 785 / 485 MB | 402 / 777 MB |
| PostGIS standing RAM | 846 MB | 0 (embedded SQLite) |
| persistent store | 128 MB PostGIS | 84.7 MB SQLite |

Equal work verified: both produce 461 359 passing times / 12 722 vehicle journeys.
