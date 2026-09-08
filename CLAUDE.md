<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

# PID-Transit

Python library (`pid-transit`, PyPI) implementing a Transmodel/NeTEx transit data
model over SQLite, with adapters that translate to and from GTFS, GTFS-Realtime,
NeTEx XML and spreadsheets.

## Governance

The rules that bind this codebase are the Conventions at the bottom of this file;
they hold whether or not Spec Kit is installed.

Spec Kit itself is a local-only development tool (`.specify/`, gitignored like
`research/`). Where it is present, `.specify/memory/constitution.md` is the long
form of those rules and `/speckit-plan` must pass its Constitution Check gate
before tasks are generated. A fresh clone will not have it — reinstall with
`specify init --here` if you want the workflow back.

## Stack

- Python 3.10+ (supported through 3.14), Pydantic v2, SQLite (stdlib `sqlite3`).
- Runtime deps: `pydantic`, `openpyxl` only. `gtfs-realtime-bindings` is the
  optional `rt` extra — the library must import without it.
- Tests: `pytest` + `hypothesis`. Build backend: `hatchling`.

## Layout

- `src/pid_transit/core/` — schemas, database, repositories, validator, geo,
  patterns, frequencies. The canonical model; no format-specific vocabulary here.
- `src/pid_transit/adapters/` — GTFS, GTFS-RT, NeTEx, spreadsheet import/export.
- `src/pid_transit/analytics/` — statistics, diff, merge, RT readiness.
- `src/pid_transit/cli.py` — thin wrapper over the `TransitDataset` facade.
- `tests/` — one module per subject; `test_roundtrip.py` guards format fidelity.
- `specs/` — Spec Kit feature artifacts, committed with the code they describe.
- `research/` — **gitignored, local only**: benchmarks, thesis material, roadmap
  docs, external validators, real test feeds. Never shipped; never promoted to
  `src/` by copy — proven ideas are rewritten into the library.

## Commands

```bash
.venv/Scripts/python -m pytest tests/ -q      # full suite
.venv/Scripts/python -m pytest tests/test_roundtrip.py -q
.venv/Scripts/python -m pid_transit.cli --help
```

## Conventions

- SQL: quote `"index"` and other reserved words used as column names; pass SPI
  references positionally where the schema expects it.
- No nested scans over feed entities — build the map once before the loop, or
  memoize (past defects: NeTEx journey-pattern rescan, validator V16/V18).
- Stream, don't materialize: `iterparse` / generators / cursor iteration on any
  path that touches a whole feed. Peak RSS must not scale with input size.
- Performance-relevant changes are measured (wall + peak RSS) on a
  production-size feed against `research/benchmark/METRICS.md`, not on fixtures.
  Report DB size and peak RSS separately — they are not the same number.
- Known open work: the exporters buffer their whole output (5.2 GB peak for a
  1.18 GB NeTEx file) while the importer streams flat at ~1.17 GB. Don't add new
  buffering paths.
