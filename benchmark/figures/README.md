# Benchmark figures

Publication-quality charts converting the text tables in `../RESULTS.md` into bar charts
and a time-vs-memory scatter, addressing the reviewer note ("convert the comparative
benchmarking results into bar charts / complex visual plots; execution time vs RAM usage
across different software").

Each figure is emitted as **PNG (300 dpi)**, **PDF** and **SVG** (vector — preferred for
LaTeX/journal submission). Regenerate with:

```bash
python make_figures.py     # needs matplotlib; data are embedded + sourced from the JSONs
```

Colours are fixed and colourblind-safe (Okabe–Ito): PID-Transit = blue, chouette-core =
vermillion, Theoremus = green.

| File | Maps to | Shows |
|---|---|---|
| `fig1_pidtransit_pipeline.*` | Table 7 (PID-Transit self-benchmark) | T_exec and peak RAM for the 4 pipeline operations |
| `fig2_theoremus_headtohead.*` | Table 8 (Theoremus race) | NeTEx→GTFS T_exec + RAM, Theoremus vs PID-Transit stages |
| `fig3_chouette_headtohead.*` | Table 9 (chouette-core race) | import/export T_exec (log) with ratios + stacked worker+PostGIS RAM |
| `fig4_time_vs_ram.*` | — (the reviewer's explicit ask) | time-vs-RAM scatter across all tools/operations (same machine) |
| `fig5_footprint.*` | §4 footprint table | deps / image size / standing services, three tools |

## Suggested captions

- **Fig. 1.** PID-Transit pipeline performance on the Porto feed (461 359 passing times):
  (a) execution time and (b) peak resident memory for the four operations, mean of 7 timed
  repetitions (+1 warmup); error bars = ±1 SD. AMD Ryzen 7 5700U, Ubuntu 24.04.

- **Fig. 2.** NeTEx→GTFS head-to-head, Theoremus (Go) vs PID-Transit, on identical Porto
  NeTEx input. PID-Transit's two stages and their sum are shown; it stays within ~1.5× of
  the purpose-built compiled converter while additionally producing a persistent SQLite
  database. (a) execution time, (b) peak RAM; mean of 7 reps (+1 warmup), ±1 SD.

- **Fig. 3.** System-of-record head-to-head, chouette-core vs PID-Transit, producing
  *identical* output (461 359 passing times, 12 722 journeys). (a) execution time
  (log scale; ≈25× / ≈9× ratios annotated); (b) peak memory — chouette's Ruby worker plus
  the standing PostGIS server, versus PID-Transit's embedded SQLite. Mean of 3 reps (+1
  warmup), ±1 SD; same machine.

- **Fig. 4.** Execution time versus peak resident memory across tools on the Porto feed
  (same machine). PID-Transit (blue) occupies the fast, light region; chouette-core
  (vermillion) is one-to-two orders of magnitude slower, and its dashed markers add the
  +846 MB standing PostGIS server that PID-Transit's embedded store avoids.

- **Fig. 5.** Deployment footprint across the three converters: third-party dependency
  closure, container/runtime image size, and standing background services (symlog scale).
  chouette-core's services reflect the inline benchmark (PostGIS only); a production
  deployment additionally requires Redis, a delayed_job worker and the Rails web app.

> Machine note: Figs 1, 3, 4 and the PID-Transit data in 5 are from the AMD Ryzen / Ubuntu
> machine (`../MACHINE.md`); Fig 2 (Theoremus) is from the Intel / Windows machine of
> RESULTS §1. Do not mix the §2 (Windows) and §4.1 (Linux) absolute timings in one claim.
