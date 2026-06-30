# PID-Transit benchmark results

Machine: x86_64 | 16 logical CPUs | 34.6 GB RAM | Python 3.12.3

Repetitions: 7 timed (+1 warmup discarded)

| Task | Description | T_exec mean ± sd (s) | min–max (s) | RAM_peak mean / max (MB) | Artifact (MB) |
|---|---|---|---|---|---|
| `gtfs_import` | GTFS .zip -> Transmodel SQLite DB | 12.76 ± 1.87 | 11.76–16.93 | 402 / 402 | 84.7 |
| `netex_export` | Transmodel DB -> NeTEx XML (dedup on) | 10.98 ± 0.08 | 10.83–11.10 | 776 / 777 | 144.8 |
| `gtfs_export` | Transmodel DB -> GTFS .zip | 4.39 ± 1.57 | 3.75–7.94 | 423 / 424 | 4.6 |
| `netex_import` | NeTEx XML -> Transmodel SQLite DB (round-trip) | 35.57 ± 23.31 | 16.09–81.04 | 1046 / 1046 | 42.7 |
