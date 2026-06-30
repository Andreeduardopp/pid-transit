"""
Fair NeTEx -> GTFS head-to-head: PID-Transit vs Theoremus, same Porto dataset.

Both tools convert the *same* NeTEx document (PID-Transit's Porto export, upgraded
with top-level Quays + ScheduledStopPoint/QuayRef so the European-profile Theoremus
loader resolves every stop) into a complete GTFS feed (2 550 stops, 12 722 journeys,
461 359 passing times). Each tool is wrapped by the same `measure()` used for the
self-benchmark, so wall-clock and peak RSS are directly comparable.

PID-Transit's NeTEx->GTFS is two stages (import to SQLite, then export GTFS); we
report each stage and their sum, since the intermediate SQLite is the persistent,
queryable artifact Theoremus does not produce.

Usage (paths default to the session scratchpad built during the benchmark run):
    python benchmark/headtohead.py --reps 7 --warmup 1 \
        --theoremus <path-to-netex-gtfs-converter.exe> \
        --netex-xml <porto_v3.xml> --netex-zip <porto_v3.zip>
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from bench import measure, capture_env, cli, REPO_ROOT  # same dir

SCRATCH = Path("C:/Users/eduar/AppData/Local/Temp/claude/"
            "C--Users-eduar-Documents-gtfs-tool-PID-GTFS/"
            "1068745f-cf22-44fd-8b2a-42103c1879d1/scratchpad")


def stats(walls, peaks):
    return {
        "wall_mean_s": statistics.mean(walls),
        "wall_std_s": statistics.stdev(walls) if len(walls) > 1 else 0.0,
        "wall_min_s": min(walls), "wall_max_s": max(walls),
        "peak_rss_mean_mb": statistics.mean(peaks),
        "peak_rss_max_mb": max(peaks),
    }


def run_task(name, cmd, before_each, reps, warmup, timeout):
    print(f"\n=== {name} ===", flush=True)
    walls, peaks = [], []
    for i in range(warmup + reps):
        if before_each:
            before_each()
        r = measure(cmd, timeout_s=timeout)
        kind = "warmup" if i < warmup else "timed "
        print(f"  {kind} {i+1}/{warmup+reps}: {r.wall_s:6.2f}s  peak {r.peak_rss_mb:6.0f}MB  rc={r.returncode}", flush=True)
        if r.returncode != 0:
            raise RuntimeError(f"{name} failed:\n{r.stdout_tail}")
        if i >= warmup:
            walls.append(r.wall_s)
            peaks.append(r.peak_rss_mb)
    return stats(walls, peaks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--theoremus", type=Path,
                    default=SCRATCH / "netex-gtfs-converter/bin/netex-gtfs-converter.exe")
    ap.add_argument("--netex-xml", type=Path, default=SCRATCH / "bench_work/porto_v3.xml")
    ap.add_argument("--netex-zip", type=Path, default=SCRATCH / "bench_work/porto_v3.zip")
    ap.add_argument("--work", type=Path, default=SCRATCH / "bench_work")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "benchmark" / "results")
    args = ap.parse_args()

    for p in (args.theoremus, args.netex_xml, args.netex_zip):
        if not p.exists():
            sys.exit(f"missing input: {p}")

    db = args.work / "h2h_pid.db"
    gtfs_pid = args.work / "h2h_pid_gtfs.zip"
    gtfs_th = args.work / "h2h_theoremus_gtfs.zip"

    env = capture_env()
    print("Environment:", env["processor"], f"| {env['logical_cpus']} cores | Python {env['python']}")

    results = {}

    # Theoremus: NeTEx zip -> GTFS (single step)
    results["theoremus_netex_to_gtfs"] = run_task(
        "Theoremus  NeTEx -> GTFS",
        [str(args.theoremus), "-codespace", "PT", "-netex", str(args.netex_zip), "-output", str(gtfs_th)],
        lambda: gtfs_th.unlink(missing_ok=True),
        args.reps, args.warmup, args.timeout,
    )

    # PID-Transit stage 1: NeTEx -> SQLite
    results["pid_netex_import"] = run_task(
        "PID-Transit  NeTEx -> SQLite",
        cli("import", str(args.netex_xml), "-f", "netex", "--db", str(db)),
        lambda: db.unlink(missing_ok=True),
        args.reps, args.warmup, args.timeout,
    )

    # PID-Transit stage 2: SQLite -> GTFS (needs the db; build once, keep)
    measure(cli("import", str(args.netex_xml), "-f", "netex", "--db", str(db)))
    results["pid_gtfs_export"] = run_task(
        "PID-Transit  SQLite -> GTFS",
        cli("export", "--db", str(db), "-f", "gtfs", "-o", str(gtfs_pid)),
        lambda: gtfs_pid.unlink(missing_ok=True),
        args.reps, args.warmup, args.timeout,
    )

    pid_total = results["pid_netex_import"]["wall_mean_s"] + results["pid_gtfs_export"]["wall_mean_s"]

    payload = {"environment": env, "config": {"reps": args.reps, "warmup": args.warmup},
            "results": results, "pid_netex_to_gtfs_total_s": pid_total}
    (args.out / "headtohead.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def row(label, s):
        return (f"| {label} | {s['wall_mean_s']:.2f} ± {s['wall_std_s']:.2f} "
                f"| {s['peak_rss_mean_mb']:.0f} |")

    print("\n\n## Fair NeTEx -> GTFS head-to-head (Porto: 2550 stops, 12722 journeys)\n")
    print("| Tool / stage | T_exec (s) | RAM_peak (MB) |")
    print("|---|---|---|")
    print(row("Theoremus (Go)  NeTEx->GTFS, single step", results["theoremus_netex_to_gtfs"]))
    print(row("PID-Transit  NeTEx->SQLite", results["pid_netex_import"]))
    print(row("PID-Transit  SQLite->GTFS", results["pid_gtfs_export"]))
    print(f"| **PID-Transit  NeTEx->GTFS (sum)** | **{pid_total:.2f}** | (peak of stages) |")
    print(f"\nWrote {args.out / 'headtohead.json'}")


if __name__ == "__main__":
    main()
