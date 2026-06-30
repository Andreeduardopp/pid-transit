"""
Generate publication-quality figures for the comparative benchmark (RESULTS.md).

Converts the text tables (PID-Transit pipeline; Theoremus head-to-head; chouette-core
head-to-head; deployment footprint) into bar charts and a time-vs-RAM scatter, plus a
footprint plot. Outputs PNG (300 dpi), PDF and SVG into this directory.

Data are transcribed from:
  * benchmark/results_linux/pid_transit.json   (PID-Transit, AMD Ryzen / Linux)
  * benchmark/bench/chouette_results.json       (chouette-core, same machine)
  * RESULTS.md  3.4                             (Theoremus, Intel / Windows)
Run:  python make_figures.py
"""
from __future__ import annotations
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter
from pathlib import Path

OUT = Path(__file__).resolve().parent

# ---- Okabe-Ito colorblind-safe palette, fixed per software ----------------- #
C_PID   = "#0072B2"   # PID-Transit  (blue)
C_CHOU  = "#D55E00"   # chouette-core (vermillion)
C_THEO  = "#009E73"   # Theoremus    (green)
C_DB    = "#E69F00"   # DB-server standing RAM (amber, stacked on chouette)

plt.rcParams.update({
    "font.size": 11,
    "font.family": "DejaVu Sans",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": "#dddddd",
    "grid.linewidth": 0.7,
    "axes.axisbelow": True,
    "figure.dpi": 120,
})

MACHINE = "AMD Ryzen 7 5700U · 16 threads · 34.6 GiB · Ubuntu 24.04 · Porto GTFS (461 359 passing times)"


def save(fig, name):
    for ext in ("png", "pdf", "svg"):
        fig.savefig(OUT / f"{name}.{ext}", bbox_inches="tight", dpi=300)
    plt.close(fig)
    print("wrote", name)


def label_bars(ax, bars, fmt="{:.1f}", dy=0, errs=None):
    for i, b in enumerate(bars):
        h = b.get_height()
        top = h + (errs[i] if errs else 0)
        ax.annotate(fmt.format(h), (b.get_x() + b.get_width() / 2, top),
                    ha="center", va="bottom", fontsize=8.5, xytext=(0, 3 + dy),
                    textcoords="offset points")


# --------------------------------------------------------------------------- #
# Figure 1 — PID-Transit pipeline (≈ Table 7): T_exec and RAM_peak, 4 ops
# --------------------------------------------------------------------------- #
def fig_pidtransit_pipeline():
    ops   = ["GTFS\nimport", "NeTEx\nexport", "GTFS\nexport", "NeTEx\nimport"]
    t     = [12.76, 10.98, 4.39, 35.57]
    t_sd  = [1.87, 0.08, 1.57, 23.31]
    ram   = [402, 777, 423, 1046]
    x = range(len(ops))

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 4.2))
    b1 = a1.bar(x, t, yerr=t_sd, capsize=4, color=C_PID, edgecolor="black", linewidth=0.5)
    a1.set_xticks(x); a1.set_xticklabels(ops)
    a1.set_ylabel("Execution time  $T_{exec}$  (s)")
    a1.set_title("(a) Execution time", fontsize=11)
    label_bars(a1, b1, "{:.1f}", errs=t_sd)
    a1.set_ylim(0, (max(t) + max(t_sd)) * 1.15)

    b2 = a2.bar(x, ram, color=C_PID, edgecolor="black", linewidth=0.5)
    a2.set_xticks(x); a2.set_xticklabels(ops)
    a2.set_ylabel("Peak memory  RAM$_{peak}$  (MB)")
    a2.set_title("(b) Peak resident memory", fontsize=11)
    label_bars(a2, b2, "{:.0f}")
    a2.set_ylim(0, max(ram) * 1.2)

    fig.suptitle("PID-Transit pipeline performance", fontweight="bold")
    fig.text(0.5, -0.02, MACHINE, ha="center", fontsize=7.5, color="#666")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    save(fig, "fig1_pidtransit_pipeline")


# --------------------------------------------------------------------------- #
# Figure 2 — Theoremus vs PID-Transit, NeTEx -> GTFS (≈ Table 8)
# --------------------------------------------------------------------------- #
def fig_theoremus():
    labels = ["Theoremus\n(Go, 1 step)", "PID-Transit\nNeTEx→SQLite", "PID-Transit\nSQLite→GTFS",
              "PID-Transit\ntotal"]
    t      = [11.98, 14.60, 3.92, 18.53]
    t_sd   = [3.62, 0.30, 0.05, 0]
    ram    = [530, 1046, 423, 1046]
    colors = [C_THEO, C_PID, C_PID, C_PID]
    x = range(len(labels))

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.4))
    b1 = a1.bar(x, t, yerr=t_sd, capsize=4, color=colors, edgecolor="black", linewidth=0.5)
    a1.set_xticks(x); a1.set_xticklabels(labels, fontsize=8.5)
    a1.set_ylabel("Execution time  $T_{exec}$  (s)")
    a1.set_title("(a) Execution time", fontsize=11)
    label_bars(a1, b1, "{:.1f}", errs=t_sd)
    a1.set_ylim(0, (max(t) + max(t_sd)) * 1.18)

    b2 = a2.bar(x, ram, color=colors, edgecolor="black", linewidth=0.5)
    a2.set_xticks(x); a2.set_xticklabels(labels, fontsize=8.5)
    a2.set_ylabel("Peak memory  RAM$_{peak}$  (MB)")
    a2.set_title("(b) Peak resident memory", fontsize=11)
    label_bars(a2, b2, "{:.0f}")
    a2.set_ylim(0, max(ram) * 1.2)

    fig.suptitle("NeTEx → GTFS head-to-head: Theoremus vs PID-Transit", fontweight="bold")
    fig.text(0.5, -0.02, "Intel / Windows machine (RESULTS §3.4) · same Porto NeTEx input · 7 reps + 1 warmup",
             ha="center", fontsize=7.5, color="#666")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    save(fig, "fig2_theoremus_headtohead")


# --------------------------------------------------------------------------- #
# Figure 3 — chouette-core vs PID-Transit (≈ Table 9): the system-of-record race
# --------------------------------------------------------------------------- #
def fig_chouette():
    ops = ["GTFS import", "NeTEx export"]
    chou_t = [323.67, 98.01]; chou_t_sd = [6.13, 0.76]
    pid_t  = [12.76, 10.98];  pid_t_sd  = [1.87, 0.08]
    chou_worker = [785, 485]
    chou_db     = [846, 846]      # PostGIS container peak (standing cost)
    pid_ram     = [402, 777]      # embedded SQLite, no DB server
    import numpy as np
    x = np.arange(len(ops)); w = 0.36

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.5))

    # (a) time, log scale
    b1 = a1.bar(x - w/2, chou_t, w, yerr=chou_t_sd, capsize=4, color=C_CHOU,
                edgecolor="black", linewidth=0.5, label="chouette-core")
    b2 = a1.bar(x + w/2, pid_t, w, yerr=pid_t_sd, capsize=4, color=C_PID,
                edgecolor="black", linewidth=0.5, label="PID-Transit")
    a1.set_yscale("log")
    a1.set_xticks(x); a1.set_xticklabels(ops)
    a1.set_ylabel("Execution time  $T_{exec}$  (s, log scale)")
    a1.set_title("(a) Execution time", fontsize=11)
    a1.legend(frameon=False, fontsize=9)
    for bars in (b1, b2):
        for b in bars:
            a1.annotate(f"{b.get_height():.0f} s", (b.get_x()+b.get_width()/2, b.get_height()),
                        ha="center", va="bottom", fontsize=8.5, xytext=(0, 2), textcoords="offset points")
    # ratio annotations
    for i, (ct, pt) in enumerate(zip(chou_t, pid_t)):
        a1.annotate(f"≈{ct/pt:.0f}×", (i, ct*1.6), ha="center", fontsize=10, fontweight="bold", color=C_CHOU)
    a1.set_ylim(1, 900)

    # (b) memory: chouette worker + PostGIS standing (stacked) vs PID embedded
    bw1 = a2.bar(x - w/2, chou_worker, w, color=C_CHOU, edgecolor="black", linewidth=0.5,
                 label="chouette: Ruby worker")
    bw2 = a2.bar(x - w/2, chou_db, w, bottom=chou_worker, color=C_DB, edgecolor="black",
                 linewidth=0.5, label="chouette: PostGIS server (standing)")
    bp  = a2.bar(x + w/2, pid_ram, w, color=C_PID, edgecolor="black", linewidth=0.5,
                 label="PID-Transit (embedded SQLite)")
    a2.set_xticks(x); a2.set_xticklabels(ops)
    a2.set_ylabel("Peak resident memory  (MB)")
    a2.set_title("(b) Memory: worker + DB-server vs embedded", fontsize=11)
    a2.legend(frameon=False, fontsize=8.5, loc="upper right")
    for i in range(len(ops)):
        total = chou_worker[i] + chou_db[i]
        a2.annotate(f"{total:.0f}", (i - w/2, total), ha="center", va="bottom",
                    fontsize=8.5, xytext=(0, 2), textcoords="offset points", fontweight="bold")
        a2.annotate(f"{pid_ram[i]:.0f}", (i + w/2, pid_ram[i]), ha="center", va="bottom",
                    fontsize=8.5, xytext=(0, 2), textcoords="offset points")
    a2.set_ylim(0, 1700)

    fig.suptitle("System-of-record head-to-head: chouette-core vs PID-Transit (identical output)",
                 fontweight="bold")
    fig.text(0.5, -0.02, MACHINE, ha="center", fontsize=7.5, color="#666")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    save(fig, "fig3_chouette_headtohead")


# --------------------------------------------------------------------------- #
# Figure 4 — Execution time vs. peak RAM across software (the reviewer's ask)
# --------------------------------------------------------------------------- #
def fig_time_vs_ram():
    # (label, time_s, worker_ram_mb, software, db_server_mb_or_0)
    pts = [
        ("GTFS import",  12.76, 402, "pid", 0),
        ("NeTEx export", 10.98, 777, "pid", 0),
        ("GTFS export",   4.39, 423, "pid", 0),
        ("NeTEx import", 35.57, 1046, "pid", 0),
        ("GTFS import",  323.67, 785, "chou", 846),
        ("NeTEx export",  98.01, 468, "chou", 846),
    ]
    col = {"pid": C_PID, "chou": C_CHOU}
    fig, ax = plt.subplots(figsize=(8.8, 5.2))

    for label, t, ram, sw, db in pts:
        c = col[sw]
        ax.scatter(t, ram, s=130, color=c, edgecolor="black", linewidth=0.6, zorder=3)
        # ghost marker + connector showing true footprint incl. DB server
        if db:
            ax.scatter(t, ram + db, s=130, facecolor="none", edgecolor=c, linewidth=1.2,
                       linestyle="--", zorder=3)
            ax.plot([t, t], [ram, ram + db], color=c, linestyle=":", linewidth=1.0, zorder=2)
        off = (8, 6)
        ax.annotate(label, (t, ram), fontsize=8.5, xytext=off, textcoords="offset points")

    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.set_xticks([5, 10, 20, 50, 100, 200, 400])
    ax.set_xlabel("Execution time  $T_{exec}$  (s, log scale)")
    ax.set_ylabel("Peak resident memory  (MB)")
    ax.set_ylim(0, 1750)
    ax.set_xlim(3, 600)

    # legend
    from matplotlib.lines import Line2D
    leg = [
        Line2D([0],[0], marker="o", color="w", markerfacecolor=C_PID, markeredgecolor="black",
               markersize=11, label="PID-Transit (embedded SQLite)"),
        Line2D([0],[0], marker="o", color="w", markerfacecolor=C_CHOU, markeredgecolor="black",
               markersize=11, label="chouette-core (Ruby worker)"),
        Line2D([0],[0], marker="o", color="w", markerfacecolor="none", markeredgecolor=C_CHOU,
               markersize=11, linestyle="--", label="chouette-core incl. PostGIS server (+846 MB)"),
    ]
    ax.legend(handles=leg, frameon=False, fontsize=9, loc="upper left")

    # quadrant guidance text
    ax.annotate("← faster, lighter", (4.5, 60), fontsize=9, color="#555", style="italic")
    ax.set_title("Execution time vs. peak memory across tools", fontweight="bold")
    fig.text(0.5, -0.01, MACHINE, ha="center", fontsize=7.5, color="#666")
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    save(fig, "fig4_time_vs_ram")


# --------------------------------------------------------------------------- #
# Figure 5 — Deployment footprint asymmetry (§4)
# --------------------------------------------------------------------------- #
def fig_footprint():
    import numpy as np
    metrics = ["3rd-party deps\n(closure)", "Container/runtime\nimage (MB)", "Standing\nservices"]
    pid  = [7, 60, 0]       # 7-pkg closure; python-slim+pure-py ~60MB; 0 services
    chou = [348, 2660, 1]   # 348-gem closure; app 1580+gems 474+PostGIS 609; 1 service (inline bench)
    theo = [0, 5, 0]        # static Go binary ~5MB; 0 deps; 0 services
    x = np.arange(len(metrics)); w = 0.26

    fig, ax = plt.subplots(figsize=(9, 4.6))
    b1 = ax.bar(x - w, pid,  w, color=C_PID,  edgecolor="black", linewidth=0.5, label="PID-Transit")
    b2 = ax.bar(x,     chou, w, color=C_CHOU, edgecolor="black", linewidth=0.5, label="chouette-core")
    b3 = ax.bar(x + w, theo, w, color=C_THEO, edgecolor="black", linewidth=0.5, label="Theoremus")
    ax.set_yscale("symlog")
    ax.set_xticks(x); ax.set_xticklabels(metrics)
    ax.set_ylabel("count / MB  (symlog scale)")
    ax.legend(frameon=False, fontsize=9)
    for bars in (b1, b2, b3):
        for b in bars:
            h = b.get_height()
            ax.annotate(f"{h:.0f}", (b.get_x()+b.get_width()/2, h), ha="center", va="bottom",
                        fontsize=8, xytext=(0, 2), textcoords="offset points")
    ax.set_title("Deployment footprint: same role, order-of-magnitude less infrastructure",
                 fontweight="bold")
    fig.text(0.5, -0.02, "chouette services shown for the INLINE benchmark (PostGIS only); "
             "production adds Redis + a delayed_job worker + the Rails web app",
             ha="center", fontsize=7.5, color="#666")
    fig.tight_layout()
    save(fig, "fig5_footprint")


if __name__ == "__main__":
    fig_pidtransit_pipeline()
    fig_theoremus()
    fig_chouette()
    fig_time_vs_ram()
    fig_footprint()
    print("\nAll figures written to", OUT)
