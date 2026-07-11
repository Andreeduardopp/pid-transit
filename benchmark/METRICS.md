# Import/Export Performance Metrics

Measured 2026-07-11 at commit `e49665c` (schema 3.2 + validator/importer
performance pass). Complements `RESULTS.md` (June comparative benchmarks);
numbers are **not** directly comparable to those — schema 3.2 added lossless
`extras` round-tripping to every adapter, and these runs write file-backed
SQLite databases instead of `:memory:`.

## Method

- Each phase runs in its **own process**, sequentially, on an otherwise idle
  machine (concurrent load inflates timings).
- **Wall** = `time.perf_counter` around the adapter call.
  **CPU** = `time.process_time` (user+sys of the process; ratio to wall < 1
  means I/O wait). **Peak RSS** = `psutil` `peak_wset` (Windows process
  high-water mark), which includes the Python runtime (~30 MB floor).
  psutil is a harness-only dependency; the package itself still depends only
  on pydantic + openpyxl.
- Short phases repeated (3× / 2×) and reported as mean; long phases run once.
- Hardware: Intel Core Ultra 7 265H (16 cores), 31.5 GB RAM, NVMe SSD,
  Windows 11 Home, CPython 3.12.1 (64-bit). All runs single-threaded.

## Datasets

| Dataset | Source size | Contents |
|---|---:|---|
| Porto GTFS (`gtfs_feed (1).zip`) | 5.1 MB zip | 71 lines, 12,722 journeys, 2,550 stops, 461,359 passing times |
| Swiss NeTEx subset (6 files of `prod_netex.zip`) | 630 MB XML | all 5 reference files + 1 timetable file: 4,145 lines, 67,183 stops, 14,085 journeys, 293,820 passing times, 3.7 M calendar exceptions |
| Swiss NeTEx full (`prod_netex.zip`) | 420 MB zip / 24.5 GB XML, 355 files | Swiss national timetable 2026: ~1.66 M journeys, ~26.5 M passing times |

## GTFS (Porto feed)

| Phase | Wall | CPU | Peak RSS | Input → Output |
|---|---:|---:|---:|---|
| Import (zip → SQLite) | 32.1 s (n=3) | 29.0 s | 557 MB | 5.1 MB zip → 104.4 MB db |
| Export (SQLite → zip) | 12.7 s (n=3) | 12.2 s | 646 MB | 104.4 MB db → 5.0 MB zip |

## NeTEx (Porto feed, round-trip through our own dialect)

| Phase | Wall | CPU | Peak RSS | Input → Output |
|---|---:|---:|---:|---|
| Export (SQLite → XML) | 45.7 s (n=2) | 42.7 s | 958 MB | 104.4 MB db → 151.4 MB XML |
| Import (XML → SQLite) | 38.1 s (n=2) | 34.5 s | **80 MB** | 151.4 MB XML → 57.9 MB db |

## NeTEx (Swiss subset, real SKI-profile data)

| Phase | Wall | CPU | Peak RSS | Input → Output |
|---|---:|---:|---:|---|
| Import (6 XML files → SQLite) | 251.4 s | 195.9 s | 1,171 MB | 630 MB XML → 516.6 MB db |
| NeTEx export (SQLite → XML) | 156.6 s | 146.8 s | **5,238 MB** | 516.6 MB db → 1.18 GB XML |
| GTFS export (SQLite → zip) | 31.7 s | 30.8 s | 2,900 MB | 516.6 MB db → 14.4 MB zip |

## NeTEx (Swiss full feed, 355 files)

| Phase | Wall | CPU | Peak RSS | Input → Output |
|---|---:|---:|---:|---|
| Import (zip → SQLite) | 6,501 s (108.4 min) | 2,751 s (45.9 min) | 1,171 MB | 420 MB zip (24.5 GB XML) → 8.64 GB db |

## Observations

- **The NeTEx importer streams; the exporters buffer.** Import peak RSS stays
  flat (~80 MB on Porto, ~1.2 GB on the subset — dominated by lookup maps and
  the 3.7 M-row exception buffer, not by XML). The NeTEx exporter builds the
  whole ElementTree in memory: 5.2 GB peak for a 1.18 GB output. Extrapolated,
  a full-Swiss NeTEx or GTFS export would exhaust RAM — the streaming-export
  rework flagged in `IMPROVEMENTS.md` is what unlocks it.
- **CPU ≈ 78–97 % of wall** on feeds whose database fits the OS page cache:
  the pipeline is CPU-bound (parsing + row building) there. The full Swiss
  import flips to **disk-bound** (CPU is only 42 % of wall) once the 8.6 GB
  database plus WAL outgrows the cache — memory itself stays flat at 1.17 GB,
  identical to the 630 MB subset, confirming the streaming design scales.
- Import wall on the Swiss subset was 93 s to `:memory:` during development
  vs 251 s file-backed here — the difference is SQLite disk I/O for a 517 MB
  database plus its WAL, not parsing.
- These numbers include the 2026-07-10/11 importer optimizations (pass-2
  skip of journey-less files, single-walk element dispatch): the same subset
  imported in 136.5 s to `:memory:` before them (now 93 s, −32 %).
- Compared with the June `RESULTS.md` runs, GTFS import grew ~9 s → ~32 s:
  that is the price of schema 3.2's lossless `extras` capture (every unmapped
  source column is preserved as JSON per row) plus file-backed output; the
  paper benchmarks should be re-run if they are to describe the current code.
