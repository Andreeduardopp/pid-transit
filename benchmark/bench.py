"""
PID-Transit comparative benchmark harness.

Measures, for each pipeline task, on a single machine and over N repetitions:

  * T_exec     -- wall-clock seconds for the whole subprocess (parse + map +
                  serialize, including interpreter/runtime startup, which is a
                  fair part of "invoking the tool").
  * RAM_peak   -- peak resident memory of the process. On Windows this is read
                  from the OS as `peak_wset` (peak working set), which is
                  monotonic and captured by the kernel, so it cannot be missed
                  by a slow sampler. On POSIX we sample RSS at a fixed interval.
  * Artifact   -- size on disk of the produced output (DB file / NeTEx XML /
                  GTFS zip). Reported separately from RAM_peak: these are
                  different quantities and conflating them is a common error.

Every tool (PID-transit and the external competitors) is run the same way:
as a subprocess, wrapped by the same `measure()` function, so the numbers are
directly comparable. psutil is a *harness* dependency only -- it is never
imported by the pid_transit package, which keeps its two-dependency footprint
intact.

Usage:
    python benchmark/bench.py --reps 7 --warmup 1
    python benchmark/bench.py --tasks gtfs_import,netex_export --reps 10
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FEED = REPO_ROOT / "gtfs_feed (1).zip"
PY = sys.executable
IS_WINDOWS = platform.system() == "Windows"


# --------------------------------------------------------------------------- #
# Measurement primitive
# --------------------------------------------------------------------------- #
@dataclass
class RunResult:
    wall_s: float
    peak_rss_mb: float
    returncode: int
    stdout_tail: str = ""


def _peak_rss_bytes(proc: psutil.Process) -> int:
    """Best available peak-RSS reading for a single process snapshot."""
    mi = proc.memory_info()
    # Windows exposes a kernel-maintained peak working set; prefer it.
    return getattr(mi, "peak_wset", mi.rss)


def measure(cmd: list[str], cwd: Path = REPO_ROOT, poll_s: float = 0.01,
            timeout_s: float | None = None) -> RunResult:
    """Run `cmd` as a subprocess; return wall time + peak RSS.

    If `timeout_s` is exceeded the whole process tree is killed and the run is
    reported with a non-zero returncode, so a hung/pathological task cannot
    block the rest of the suite.
    """
    t0 = time.perf_counter()
    # Decode as UTF-8 with replacement: external tools (e.g. the Go converter) emit
    # emoji/box-drawing output that the Windows default cp1252 codec cannot decode,
    # which otherwise raises in the stdout reader thread.
    proc = psutil.Popen(cmd, cwd=str(cwd), stdout=psutil.subprocess.PIPE,
                        stderr=psutil.subprocess.STDOUT, text=True,
                        encoding="utf-8", errors="replace")
    peak = 0

    def poll():
        nonlocal peak
        while proc.poll() is None:
            try:
                peak = max(peak, _peak_rss_bytes(proc))
                for child in proc.children(recursive=True):
                    peak = max(peak, _peak_rss_bytes(child))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(poll_s)

    t = threading.Thread(target=poll, daemon=True)
    t.start()
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except psutil.subprocess.TimeoutExpired:
        for child in proc.children(recursive=True):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        proc.kill()
        out, _ = proc.communicate()
        out = (out or "") + f"\n[TIMEOUT after {timeout_s}s -- killed]"
    t.join(timeout=1.0)
    # Final reading (peak_wset is monotonic; this catches a peak just before exit
    # on Windows even if the poller missed it).
    try:
        peak = max(peak, _peak_rss_bytes(proc))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    wall = time.perf_counter() - t0

    tail = "\n".join((out or "").strip().splitlines()[-3:])
    return RunResult(wall_s=wall, peak_rss_mb=peak / 1024 / 1024,
                    returncode=proc.returncode, stdout_tail=tail)


# --------------------------------------------------------------------------- #
# Task definitions -- each returns (cmd, artifact_path, setup_fn)
# --------------------------------------------------------------------------- #
def cli(*args: str) -> list[str]:
    return [PY, "-m", "pid_transit.cli", *args]


@dataclass
class Task:
    name: str
    description: str
    build_cmd: "callable"          # (paths) -> list[str]
    artifact: "callable"           # (paths) -> Path
    setup: "callable" = None       # (paths) -> None, run once before timed reps
    before_each: "callable" = None # (paths) -> None, run before every rep


def make_tasks(feed: Path, work: Path) -> dict[str, Task]:
    ref_db = work / "ref.db"          # prebuilt, read by export tasks
    netex_out = work / "out_netex.xml"
    gtfs_out = work / "out_gtfs.zip"
    imp_db = work / "import.db"
    netex_in = work / "netex_input.xml"
    netex_imp_db = work / "netex_import.db"

    def build_ref_db(_):
        if ref_db.exists():
            ref_db.unlink()
        r = measure(cli("import", str(feed), "-f", "gtfs", "--db", str(ref_db)))
        if r.returncode != 0:
            raise RuntimeError(f"ref db build failed:\n{r.stdout_tail}")

    def build_netex_input(_):
        build_ref_db(_)
        if netex_in.exists():
            netex_in.unlink()
        r = measure(cli("export", "--db", str(ref_db), "-f", "netex", "-o", str(netex_in)))
        if r.returncode != 0:
            raise RuntimeError(f"netex input build failed:\n{r.stdout_tail}")

    def rm(p: Path):
        return lambda _: p.unlink(missing_ok=True)

    return {
        "gtfs_import": Task(
            name="gtfs_import",
            description="GTFS .zip -> Transmodel SQLite DB",
            build_cmd=lambda: cli("import", str(feed), "-f", "gtfs", "--db", str(imp_db)),
            artifact=lambda: imp_db,
            before_each=rm(imp_db),
        ),
        "netex_export": Task(
            name="netex_export",
            description="Transmodel DB -> NeTEx XML (dedup on)",
            build_cmd=lambda: cli("export", "--db", str(ref_db), "-f", "netex", "-o", str(netex_out)),
            artifact=lambda: netex_out,
            setup=build_ref_db,
            before_each=rm(netex_out),
        ),
        "gtfs_export": Task(
            name="gtfs_export",
            description="Transmodel DB -> GTFS .zip",
            build_cmd=lambda: cli("export", "--db", str(ref_db), "-f", "gtfs", "-o", str(gtfs_out)),
            artifact=lambda: gtfs_out,
            setup=build_ref_db,
            before_each=rm(gtfs_out),
        ),
        "netex_import": Task(
            name="netex_import",
            description="NeTEx XML -> Transmodel SQLite DB (round-trip)",
            build_cmd=lambda: cli("import", str(netex_in), "-f", "netex", "--db", str(netex_imp_db)),
            artifact=lambda: netex_imp_db,
            setup=build_netex_input,
            before_each=rm(netex_imp_db),
        ),
    }


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
@dataclass
class TaskSummary:
    name: str
    description: str
    reps: int
    wall_mean_s: float
    wall_std_s: float
    wall_min_s: float
    wall_max_s: float
    peak_rss_mean_mb: float
    peak_rss_max_mb: float
    artifact_mb: float
    raw: list = field(default_factory=list)


def summarize(name, desc, results: list[RunResult], artifact_mb: float) -> TaskSummary:
    walls = [r.wall_s for r in results]
    peaks = [r.peak_rss_mb for r in results]
    return TaskSummary(
        name=name, description=desc, reps=len(results),
        wall_mean_s=statistics.mean(walls),
        wall_std_s=statistics.stdev(walls) if len(walls) > 1 else 0.0,
        wall_min_s=min(walls), wall_max_s=max(walls),
        peak_rss_mean_mb=statistics.mean(peaks),
        peak_rss_max_mb=max(peaks),
        artifact_mb=artifact_mb,
        raw=[{"wall_s": round(r.wall_s, 4), "peak_rss_mb": round(r.peak_rss_mb, 1)}
            for r in results],
    )


def capture_env() -> dict:
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "logical_cpus": psutil.cpu_count(logical=True),
        "physical_cpus": psutil.cpu_count(logical=False),
        "total_ram_gb": round(psutil.virtual_memory().total / 1024**3, 1),
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def markdown_table(summaries: list[TaskSummary]) -> str:
    lines = [
        "| Task | Description | T_exec mean ± sd (s) | min–max (s) | RAM_peak mean / max (MB) | Artifact (MB) |",
        "|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| `{s.name}` | {s.description} | {s.wall_mean_s:.2f} ± {s.wall_std_s:.2f} "
            f"| {s.wall_min_s:.2f}–{s.wall_max_s:.2f} "
            f"| {s.peak_rss_mean_mb:.0f} / {s.peak_rss_max_mb:.0f} "
            f"| {s.artifact_mb:.1f} |"
        )
    return "\n".join(lines)


def run(tasks: dict[str, Task], selected: list[str], reps: int, warmup: int,
        work: Path, timeout_s: float | None = None, on_done=None) -> list[TaskSummary]:
    summaries = []
    for name in selected:
        task = tasks[name]
        print(f"\n=== {name}: {task.description} ===", flush=True)
        if task.setup:
            print("  [setup]...", flush=True)
            task.setup(None)
        results: list[RunResult] = []
        total = warmup + reps
        for i in range(total):
            if task.before_each:
                task.before_each(None)
            r = measure(task.build_cmd(), timeout_s=timeout_s)
            kind = "warmup" if i < warmup else "timed "
            print(f"  {kind} {i+1}/{total}: {r.wall_s:6.2f}s  "
                f"peak {r.peak_rss_mb:6.0f}MB  rc={r.returncode}", flush=True)
            if r.returncode != 0:
                raise RuntimeError(f"{name} failed:\n{r.stdout_tail}")
            if i >= warmup:
                results.append(r)
        artifact = task.artifact()
        artifact_mb = artifact.stat().st_size / 1024 / 1024 if artifact.exists() else 0.0
        summaries.append(summarize(name, task.description, results, artifact_mb))
        if on_done:
            on_done(summaries)   # persist partial results after every task
    return summaries


def main():
    ap = argparse.ArgumentParser(description="PID-Transit benchmark harness")
    ap.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    ap.add_argument("--reps", type=int, default=7, help="timed repetitions")
    ap.add_argument("--warmup", type=int, default=1, help="discarded warmup runs")
    ap.add_argument("--tasks", default="gtfs_import,netex_export,gtfs_export,netex_import")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "benchmark" / "results")
    ap.add_argument("--timeout", type=float, default=300.0,
                    help="per-rep timeout in seconds; tree is killed if exceeded")
    ap.add_argument("--work", type=Path,
                    default=Path("C:/Users/eduar/AppData/Local/Temp/claude/"
                                "C--Users-eduar-Documents-gtfs-tool-PID-GTFS/"
                                "1068745f-cf22-44fd-8b2a-42103c1879d1/scratchpad/bench_work"))
    args = ap.parse_args()

    args.work.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.feed.exists():
        sys.exit(f"feed not found: {args.feed}")

    env = capture_env()
    print("Environment:")
    for k, v in env.items():
        print(f"  {k}: {v}")

    tasks = make_tasks(args.feed, args.work)
    selected = [t.strip() for t in args.tasks.split(",") if t.strip()]
    for s in selected:
        if s not in tasks:
            sys.exit(f"unknown task: {s} (have: {', '.join(tasks)})")

    summaries = run(tasks, selected, args.reps, args.warmup, args.work)

    payload = {"environment": env, "config": {"reps": args.reps, "warmup": args.warmup,
                                            "feed": str(args.feed)},
            "results": [asdict(s) for s in summaries]}
    (args.out / "pid_transit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md = "# PID-Transit benchmark results\n\n"
    md += f"Machine: {env['processor']} | {env['logical_cpus']} logical CPUs | "
    md += f"{env['total_ram_gb']} GB RAM | Python {env['python']}\n\n"
    md += f"Repetitions: {args.reps} timed (+{args.warmup} warmup discarded)\n\n"
    md += markdown_table(summaries) + "\n"
    (args.out / "pid_transit.md").write_text(md, encoding="utf-8")

    print("\n" + markdown_table(summaries))
    print(f"\nWrote {args.out / 'pid_transit.json'} and {args.out / 'pid_transit.md'}")


if __name__ == "__main__":
    main()
