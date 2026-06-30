# PID-Transit benchmark results

Machine: Intel64 Family 6 Model 197 Stepping 2, GenuineIntel | 16 logical CPUs | 31.5 GB RAM | Python 3.12.1

Repetitions: 7 timed (+1 warmup discarded)

| Task | Description | T_exec mean ± sd (s) | min–max (s) | RAM_peak mean / max (MB) | Artifact (MB) |
|---|---|---|---|---|---|
| `gtfs_import` | GTFS .zip -> Transmodel SQLite DB | 12.41 ± 4.35 | 8.67–17.95 | 403 / 403 | 84.7 |
| `netex_export` | Transmodel DB -> NeTEx XML (dedup on) | 53.94 ± 2.40 | 51.87–58.24 | 2871 / 2872 | 145.9 |
| `gtfs_export` | Transmodel DB -> GTFS .zip | 3.97 ± 0.16 | 3.79–4.20 | 425 / 427 | 4.6 |
| `netex_import` | NeTEx XML -> Transmodel SQLite DB (round-trip) | 13.33 ± 0.33 | 12.80–13.66 | 1040 / 1040 | 42.7 |
