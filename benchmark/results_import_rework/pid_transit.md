# PID-Transit benchmark results

Machine: Intel64 Family 6 Model 197 Stepping 2, GenuineIntel | 16 logical CPUs | 31.5 GB RAM | Python 3.12.1

Repetitions: 3 timed (+1 warmup discarded)

| Task | Description | T_exec mean ± sd (s) | min–max (s) | RAM_peak mean / max (MB) | Artifact (MB) |
|---|---|---|---|---|---|
| `gtfs_import` | GTFS .zip -> Transmodel SQLite DB | 7.44 ± 0.05 | 7.41–7.50 | 413 / 413 | 84.7 |
| `netex_export` | Transmodel DB -> NeTEx XML (dedup on) | 9.60 ± 0.11 | 9.48–9.68 | 791 / 791 | 151.4 |
| `gtfs_export` | Transmodel DB -> GTFS .zip | 3.46 ± 0.10 | 3.39–3.58 | 435 / 436 | 4.6 |
| `netex_import` | NeTEx XML -> Transmodel SQLite DB (round-trip) | 10.19 ± 0.16 | 10.07–10.37 | 87 / 87 | 48.3 |
