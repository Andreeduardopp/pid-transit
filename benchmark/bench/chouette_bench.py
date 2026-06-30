"""
chouette-core timed benchmark — comparable to PID-Transit's bench.py.

Each operation runs as a synchronous `rails runner` inside the app container,
wrapped by GNU `/usr/bin/time -v` so we capture:

  * T_exec (OP_SECONDS)  -- in-process monotonic time around JUST the operation
                            (import_without_status / export). This is the figure
                            comparable to PID-Transit's conversion work; it
                            excludes Rails boot, which is a fixed per-invocation
                            tax reported separately (FULL_WALL).
  * FULL_WALL            -- host-side wall of the whole docker-exec, incl. Rails
                            boot (the "cost of invoking the tool", analogous to
                            PID-Transit including Python startup).
  * RAM_peak (worker)    -- Maximum resident set size of the Ruby process from
                            /usr/bin/time -v. Directly comparable to PID-Transit
                            process RSS.

The PostGIS DB-server memory is read separately from the db container's cgroup
peak (memory.peak), reported as the standing cost of the external DB engine
that PID-Transit's embedded SQLite avoids.
"""
from __future__ import annotations
import argparse, json, re, statistics, subprocess, sys, time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
DB = "chouette-bench-db"
APP = "chouette-bench-app"


def dc(*args, timeout=1800, check=True):
    """docker compose ... in BENCH_DIR."""
    r = subprocess.run(["docker", "compose", *args], cwd=str(BENCH_DIR),
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"`docker compose {' '.join(args)}` failed rc={r.returncode}\n"
                          f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    return r


def runner(script: str, *script_args: str, timeout=1800):
    """Run `rails runner <script>` under /usr/bin/time -v; return dict."""
    t0 = time.perf_counter()
    r = dc("exec", "-T", "app", "/usr/bin/time", "-v",
           "bin/rails", "runner", script, *script_args, timeout=timeout, check=False)
    full_wall = time.perf_counter() - t0
    out, err = r.stdout, r.stderr
    rss_kb = _grep_float(err, r"Maximum resident set size \(kbytes\):\s*([\d.]+)")
    op_s = _grep_float(out, r"OP_SECONDS=([\d.]+)")
    return {
        "rc": r.returncode,
        "full_wall_s": full_wall,
        "op_s": op_s,
        "worker_rss_mb": (rss_kb / 1024.0) if rss_kb is not None else None,
        "stdout": out,
        "stderr_tail": "\n".join(err.strip().splitlines()[-4:]),
    }


def _grep_float(text: str, pat: str):
    m = re.search(pat, text)
    return float(m.group(1)) if m else None


def db_cgroup_peak_mb():
    """Peak memory of the PostGIS container cgroup (monotonic), in MB."""
    r = dc("exec", "-T", "db", "sh", "-lc",
           "cat /sys/fs/cgroup/memory.peak 2>/dev/null || cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes 2>/dev/null",
           check=False)
    v = r.stdout.strip().splitlines()
    try:
        return int(v[0]) / 1024 / 1024
    except Exception:
        return None


def db_reset_peak():
    dc("exec", "-T", "db", "sh", "-lc", "echo 0 > /sys/fs/cgroup/memory.peak 2>/dev/null || true", check=False)


def stat_block(name, runs):
    op = [r["op_s"] for r in runs if r["op_s"] is not None]
    fw = [r["full_wall_s"] for r in runs]
    rss = [r["worker_rss_mb"] for r in runs if r["worker_rss_mb"] is not None]
    def ms(xs): return {
        "mean": statistics.mean(xs), "sd": statistics.stdev(xs) if len(xs) > 1 else 0.0,
        "min": min(xs), "max": max(xs)} if xs else None
    return {"name": name, "reps": len(runs), "op_s": ms(op), "full_wall_s": ms(fw),
            "worker_rss_mb": ms(rss), "raw": runs}


def timed(name, script, script_args, reps, warmup, before_each=None, timeout=1800):
    print(f"\n=== {name} ===", flush=True)
    runs = []
    for i in range(warmup + reps):
        if before_each:
            before_each()
        r = runner(script, *script_args, timeout=timeout)
        kind = "warmup" if i < warmup else "timed "
        print(f"  {kind} {i+1}/{warmup+reps}: op={r['op_s']}s full={r['full_wall_s']:.2f}s "
              f"worker_rss={r['worker_rss_mb']:.0f}MB rc={r['rc']}", flush=True)
        if r["rc"] != 0:
            print("  STDOUT:\n" + r["stdout"], flush=True)
            print("  STDERR_TAIL:\n" + r["stderr_tail"], flush=True)
            raise RuntimeError(f"{name} rep failed (rc={r['rc']})")
        if i >= warmup:
            runs.append(r)
    return stat_block(name, runs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--profile", default="european", help="netex export profile (european|none)")
    ap.add_argument("--out", type=Path, default=BENCH_DIR / "chouette_results.json")
    args = ap.parse_args()

    print("Seeding domain graph (once)...", flush=True)
    seed = dc("exec", "-T", "app", "bin/rails", "runner", "/bench/seed.rb")
    print(seed.stdout.strip(), flush=True)

    db_reset_peak()
    results = {}

    # GTFS import: each rep creates a fresh referential from the same feed.
    results["gtfs_import"] = timed(
        "chouette GTFS import (GTFS -> PostGIS Referential)",
        "/bench/import.rb", [], args.reps, args.warmup)

    # Use the last import's referential as the stable export source.
    ref_id = dc("exec", "-T", "app", "sh", "-lc", "cat /out/referential_id").stdout.strip()
    print(f"\nExport source referential_id = {ref_id}", flush=True)

    results["netex_export"] = timed(
        f"chouette NeTEx export (Referential -> NeTEx, profile={args.profile})",
        "/bench/export.rb", [ref_id, args.profile], args.reps, args.warmup)

    db_peak = db_cgroup_peak_mb()

    # Artifact + DB size facts.
    art = dc("exec", "-T", "app", "sh", "-lc",
             "ls -l /out/chouette_netex_* 2>/dev/null | awk '{print $5, $9}'").stdout.strip()
    dbsize = dc("exec", "-T", "db", "psql", "-U", "chouette", "-d", "chouette2", "-tAc",
                "SELECT pg_size_pretty(pg_database_size('chouette2'));", check=False).stdout.strip()

    payload = {"config": vars(args) | {"out": str(args.out)},
               "db_cgroup_peak_mb": db_peak, "db_size": dbsize, "artifact": art,
               "results": results}
    args.out.write_text(json.dumps(payload, indent=2, default=str))

    print("\n\n================ CHOUETTE RESULTS ================")
    for k, s in results.items():
        op = s["op_s"]; rss = s["worker_rss_mb"]; fw = s["full_wall_s"]
        print(f"{k}: op {op['mean']:.2f}±{op['sd']:.2f}s | full(incl boot) {fw['mean']:.2f}s "
              f"| worker RSS {rss['max']:.0f}MB | reps {s['reps']}")
    print(f"PostGIS container peak: {db_peak:.0f}MB | DB size: {dbsize} | artifact: {art}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
