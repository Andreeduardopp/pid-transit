# Benchmark machine — specification (Linux / chouette-core head-to-head)

This is the machine used for the **chouette-core timed head-to-head** and the
**same-machine PID-Transit re-baseline** reported in `RESULTS.md` §4.1. It is a
*different* host from the Windows/Intel machine in `RESULTS.md` §1 (which was used for the
original §2 self-benchmark and the Theoremus head-to-head); the chouette comparison and its
PID-Transit baseline were both run here so they are hardware-fair to each other.

## Hardware

| Component | Specification |
|---|---|
| CPU | **AMD Ryzen 7 5700U** (Zen 2, "Lucienne", 7 nm) — **8 cores / 16 threads** |
| CPU clocks | 1.8 GHz base / up to 4.3 GHz boost (vendor nominal) |
| CPU cache | L1 512 KiB, L2 4 MiB, L3 8 MiB |
| Architecture | x86-64 (AVX2, AES-NI, SHA-NI) |
| RAM | **≈ 34.6 GiB** total (36 266 984 kB) |
| Storage | **NVMe SSD** — ADATA LEGEND 710, 477 GB (non-rotational) |
| Platform | Bare metal (not virtualized) |

## Software

| Layer | Version |
|---|---|
| OS | **Ubuntu 24.04.2 LTS** |
| Kernel | Linux **6.17.0-35-generic** (x86_64) |
| Container engine | **Docker 29.0.0** + Compose **v2.40.3** |
| Benchmark harness runtime | **CPython 3.12.3** (isolated venv; `psutil` for RSS sampling) |
| System Python | CPython 3.11.14 |
| PostgreSQL client | 17.5 |

### Containerized stack (chouette-core, §4.1)
| Container | Image / runtime |
|---|---|
| Database | `postgis/postgis:16-3.4` — **PostgreSQL 16 + PostGIS 3.4** |
| Application | custom image from public `ruby:3.4` → **Ruby 3.4.9** + chouette-core (master), 347 gems |

### PID-Transit runtime (same machine)
- **CPython 3.12.3**, package installed editable; 2 declared dependencies (`pydantic`, `openpyxl`).

## Dataset (common input to both tools)

**Porto metropolitan GTFS** — `gtfs_feed (1).zip`, **5.07 MB** (5 320 098 bytes), feed dated
2026-04-30:

| Entity | Count |
|---|---|
| Routes (lines) | 71 |
| Stops | 2 550 |
| Trips (service journeys) | 12 722 |
| Stop times (passing times) | 461 359 |

## Methodology note
Each task is run as an isolated process and timed over **N = 3 repetitions + 1 discarded
warmup** (chouette-core, §4.1) / **N = 7 + 1 warmup** (PID-Transit self-benchmark).
`T_exec` is wall-clock of the operation; for chouette-core it is the in-process operation
time (excluding the ~5 s Rails boot, reported separately). `RAM_peak` is peak process RSS
(GNU `/usr/bin/time -v` inside the container for the Ruby worker; `psutil` peak sampling for
PID-Transit). The PostGIS server's peak memory is read from the container cgroup
(`memory.peak`) and reported separately as the standing cost of the external DB engine.

---

### Ready-to-paste prose (for the thesis text)

> All comparative measurements in this section were obtained on an AMD Ryzen 7 5700U
> workstation (8 cores / 16 threads, ≈ 34.6 GiB RAM, NVMe SSD) running Ubuntu 24.04.2 LTS
> (kernel 6.17). chouette-core was deployed via Docker 29.0 (Compose v2.40) as a two-
> container stack — PostgreSQL 16 with PostGIS 3.4 and a Ruby 3.4.9 application container
> built from chouette-core master — while PID-Transit ran natively under CPython 3.12.3 on
> the same host, so that the two systems are compared on identical hardware. The common
> input was the Porto metropolitan GTFS feed (5.07 MB; 71 lines, 2 550 stops, 12 722 service
> journeys, 461 359 passing times).
