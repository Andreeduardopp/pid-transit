# PID-Transit benchmark results

Machine: Intel64 Family 6 Model 197 Stepping 2, GenuineIntel | 16 logical CPUs | 31.5 GB RAM | Python 3.12.1

Repetitions: 10 timed (+2 warmup discarded)

| Task | Description | T_exec mean ± sd (s) | min–max (s) | RAM_peak mean / max (MB) | Artifact (MB) |
|---|---|---|---|---|---|
| `gtfs_import` | GTFS .zip -> Transmodel SQLite DB | 9.05 ± 0.13 | 8.91–9.32 | 403 / 403 | 84.7 |
