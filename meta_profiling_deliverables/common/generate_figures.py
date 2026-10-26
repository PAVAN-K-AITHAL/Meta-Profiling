#!/usr/bin/env python3
"""
generate_figures.py — Publication-Quality Figure Generation

Reads CSV outputs from run_hypotheses_dummy.py and run_hypotheses_goapi.py,
generates all figures required for the paper as PDF (vector graphics).

Figures:
  1. H2 scaling curve: overhead vs sampling frequency (1/c)
  2. H3 ablation bar chart: with-g vs without-g cost decomposition
  3. H4 equivalence plot: 90% CI relative to ±1% bounds
  4. H1 cross-workload comparison bar chart
  5. Architecture diagram (schematic)

Usage:
  python3 generate_figures.py <dummy_csv> [--goapi <goapi_csv>] [--outdir figures/]

Requirements:
  pip install matplotlib numpy pandas scipy
"""

import argparse
import os
import sys
import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")  # non-interactive backend (for server use)
import matplotlib.pyplot as plt
import matplotlib.ticker

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_hypotheses import collect_h4_sources, overhead_percentages

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 12,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

COLORS = {
    "primary":   "#2563EB",   # blue
    "secondary": "#DC2626",   # red
    "accent":    "#16A34A",   # green
    "neutral":   "#6B7280",   # gray
    "light":     "#DBEAFE",   # light blue
}

FIG_EXT = "pdf"  # overridden by --format


def si_label(n):
    """Format a number with SI suffix."""
    if n >= 1e9: return f"{n/1e9:.1f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.0f}K"
    return str(int(n))


def save(fig, name, label):
    out = os.path.join(OUTDIR, f"{name}.{FIG_EXT}")
    fig.savefig(out, bbox_inches='tight')
    plt.close(fig)
    print(f"  ✅ {label}: {out}")


# ---------------------------------------------------------------------------
# Figure 1: H2 Scaling Curve
# ---------------------------------------------------------------------------

def plot_h2_scaling(dummy_df):
    """H2: Overhead vs sampling frequency (1/c).

    X-axis: sampling frequency = 1/c (log scale)
    Y-axis: mean cycles consumed by perf record
    Shows: data points + linear fit + quadratic fit, and marks the
    default operating point (-c 100K) used by H1/H3/H4.
    """
    h2_data = dummy_df[dummy_df['hypothesis'] == 'H2']
    h2_profiled = h2_data[h2_data['run_type'].isin(['profiled', 'tool_overhead'])]
    if h2_profiled.empty:
        print("  ⚠️ No H2 data — skipping Figure 1")
        return

    grouped = h2_profiled.groupby('config_c')['cycles']
    c_vals = sorted(grouped.groups.keys())
    means = [grouped.get_group(c).mean() for c in c_vals]
    stds  = [grouped.get_group(c).std()  for c in c_vals]
    ns    = [grouped.get_group(c).count() for c in c_vals]
    freqs = [1.0 / c for c in c_vals]

    fig, ax = plt.subplots(figsize=(7, 4.5))

    # Data points with error bars (95% CI)
    cis = [1.96 * s / np.sqrt(n) if n > 1 else 0 for s, n in zip(stds, ns)]
    ax.errorbar(freqs, means, yerr=cis, fmt='o', color=COLORS["primary"],
                markersize=7, linewidth=2, capsize=4, label="Measured mean ± 95% CI",
                zorder=5)

    x = np.array(freqs)
    y = np.array(means)
    x_fit = np.geomspace(x.min(), x.max(), 200)

    # Linear fit
    slope, intercept, r, _, _ = stats.linregress(x, y)
    ax.plot(x_fit, slope * x_fit + intercept, '--',
            color=COLORS["neutral"], linewidth=1.5,
            label=f"Linear fit (R²={r**2:.3f})")

    # Quadratic fit (needs ≥ 3 distinct points)
    if len(x) >= 3:
        coeffs = np.polyfit(x, y, 2)
        ax.plot(x_fit, np.polyval(coeffs, x_fit), '-',
                color=COLORS["secondary"], linewidth=2, label="Quadratic fit")

    # Default operating point (-c 100K)
    ax.axvline(1.0 / 100000, color=COLORS["accent"], linewidth=1, linestyle=':',
               label="Default operating point (-c 100K)")

    ax.set_xscale("log")
    ax.set_xlabel("Sampling frequency (1/c)  [log scale]", labelpad=24)
    ax.set_ylabel("perf record cycles consumed")
    ax.set_title("H2: Overhead vs. Sampling Frequency")
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: si_label(v))
    )
    ax.legend(framealpha=0.9)

    # Annotate -c values just below the x-axis (axes coords, not data y=0)
    for c, f in zip(c_vals, freqs):
        ax.annotate(f"-c {si_label(c)}", (f, 0), xycoords=ax.get_xaxis_transform(),
                    textcoords="offset points", xytext=(0, -26),
                    ha='center', fontsize=8, color=COLORS["neutral"],
                    annotation_clip=False)

    save(fig, "fig1_h2_scaling", "Figure 1")


# ---------------------------------------------------------------------------
# Figure 2: H3 Ablation Bar Chart
# ---------------------------------------------------------------------------

def plot_h3_ablation(dummy_df):
    """H3: With-g vs without-g cost decomposition.

    Stacked bar chart showing:
      - Base sampling cost (without -g)
      - Call-graph cost (with-g minus without-g)
    For each metric. Metrics differ by orders of magnitude, so each
    metric gets its own panel.
    """
    h3_data = dummy_df[dummy_df['hypothesis'] == 'H3']
    h3_profiled = h3_data[h3_data['run_type'].isin(['profiled', 'tool_overhead'])]
    if h3_profiled.empty:
        print("  ⚠️ No H3 data — skipping Figure 2")
        return

    with_g    = h3_profiled[h3_profiled['config_g'] == 'on']
    without_g = h3_profiled[h3_profiled['config_g'] == 'off']

    metrics = ['cycles', 'cache_misses', 'branch_misses']
    labels  = ['Cycles', 'L1-dcache load misses', 'Branch misses']

    fig, axes = plt.subplots(1, len(metrics), figsize=(3.2 * len(metrics), 4.5))

    for ax, metric, mlabel in zip(axes, metrics, labels):
        base = without_g[metric].dropna().mean()
        total = with_g[metric].dropna().mean()
        g_cost = max(0, total - base)

        ax.bar([0], [base], 0.6, color=COLORS["primary"], alpha=0.85,
               label="Base sampling (without -g)")
        ax.bar([0], [g_cost], 0.6, bottom=[base], color=COLORS["secondary"],
               alpha=0.85, label="Call-graph overhead (-g)")
        if total > 0:
            ax.text(0, max(total, base) * 1.02, f"-g: {g_cost / total * 100:.0f}%",
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

        ax.set_xticks([])
        ax.set_xlim(-0.8, 0.8)
        ax.set_title(mlabel, fontsize=11)
        ax.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: si_label(v))
        )

    axes[0].set_ylabel("Mean event count (perf record)")
    handles, leg_labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, leg_labels, loc='lower center', ncol=2,
               bbox_to_anchor=(0.5, -0.06), framealpha=0.9)
    fig.suptitle("H3: Call-Graph (-g) Cost Decomposition\n"
                 "(percentage = call-graph share of total)", fontsize=12)
    plt.tight_layout()

    save(fig, "fig2_h3_ablation", "Figure 2")


# ---------------------------------------------------------------------------
# Figure 3: H4 Equivalence Plot
# ---------------------------------------------------------------------------

H4_LABELS = {
    "dummy workload":       "CPU-bound (lat_mem_rd)",
    "Go API workload":      "Go microservice (HTTP API)",
    "Go API workload (H1)": "Go microservice (HTTP API)",
}


def plot_h4_equivalence(dummy_df, goapi_df):
    """H4: 90% CI for overhead % relative to ±1% equivalence bounds.

    Overhead % is computed exactly as in analyze_hypotheses.py (shared
    helpers), so the figure always matches the report. If the CI lies
    entirely within ±1%, overhead is provably negligible.
    """
    sources = collect_h4_sources(dummy_df, goapi_df)
    if not sources:
        print("  ⚠️ No H4 data — skipping Figure 3")
        return

    metrics = ['cycles', 'cache_misses', 'branch_misses']
    metric_labels = ['Cycles', 'L1-dcache load misses', 'Branch misses']
    n_sources = len(sources)
    n_metrics = len(metrics)
    src_colors = [COLORS["primary"], COLORS["secondary"], COLORS["accent"]]

    fig, axes = plt.subplots(1, n_metrics, figsize=(4 * n_metrics, 1.2 + 1.0 * n_sources),
                             sharey=True)

    for ax, metric, mlabel in zip(axes, metrics, metric_labels):
        ax.set_title(mlabel)

        for src_idx, (src_name, measured, baseline, kind) in enumerate(sources):
            _, _, pct = overhead_percentages(measured, baseline, metric, kind)
            if len(pct) < 2:
                continue

            mean_r = np.mean(pct)
            se = np.std(pct, ddof=1) / np.sqrt(len(pct))
            t_crit = stats.t.ppf(0.95, len(pct) - 1)  # 90% CI ↔ TOST α=0.05
            ci_lo, ci_hi = mean_r - t_crit * se, mean_r + t_crit * se

            color = src_colors[src_idx % len(src_colors)]
            ax.plot([ci_lo, ci_hi], [src_idx, src_idx], '-', color=color,
                    linewidth=3, solid_capstyle='round')
            ax.plot(mean_r, src_idx, 'D', color=color, markersize=8, zorder=5)
            ax.annotate(f"{mean_r:.3f}%", (mean_r, src_idx),
                        textcoords="offset points", xytext=(0, 9),
                        ha='center', fontsize=8, color=color)

        # Equivalence bounds
        ax.axvspan(-1.0, 1.0, alpha=0.07, color=COLORS["accent"])
        ax.axvline(1.0, color=COLORS["accent"], linewidth=2, linestyle='--',
                   label="±1% bound", alpha=0.8)
        ax.axvline(-1.0, color=COLORS["accent"], linewidth=2, linestyle='--',
                   alpha=0.8)
        ax.axvline(0, color='black', linewidth=0.5, alpha=0.5)
        ax.set_xlim(-1.5, 1.5)
        ax.set_ylim(-0.6, n_sources - 0.4)
        ax.set_xlabel("Overhead (% of workload)")

    axes[0].set_yticks(range(n_sources))
    axes[0].set_yticklabels([H4_LABELS.get(s[0], s[0]) for s in sources], fontsize=9)
    axes[-1].legend(loc='lower right', fontsize=8)

    fig.suptitle("H4: Production Negligibility — 90% CI vs. ±1% Equivalence Bounds",
                 fontsize=12)
    plt.tight_layout()

    save(fig, "fig3_h4_equivalence", "Figure 3")


# ---------------------------------------------------------------------------
# Figure 4: H1 Cross-Workload Comparison
# ---------------------------------------------------------------------------

def plot_h1_crossworkload(dummy_df, goapi_df):
    """H1: perf record's own hardware cost across different workloads.

    Bar chart with error bars showing perf record's cycles/misses
    measured while profiling each workload type.

    Caveat: the dummy rows cover a fixed time window (DURATION s) while
    the Go API rows cover a fixed request count, so absolute magnitudes
    are only comparable if the windows are of similar length.
    """
    groups = {}

    h1_dummy = dummy_df[dummy_df['hypothesis'] == 'H1']
    tool_dummy = h1_dummy[h1_dummy['run_type'] == 'tool_overhead']
    if not tool_dummy.empty:
        groups['CPU-bound\n(lat_mem_rd)'] = tool_dummy

    if goapi_df is not None:
        h1_goapi = goapi_df[goapi_df['hypothesis'] == 'H1']
        tool_goapi = h1_goapi[h1_goapi['run_type'] == 'tool_overhead']
        if not tool_goapi.empty:
            groups['Go microservice\n(HTTP API)'] = tool_goapi

    if not groups:
        print("  ⚠️ Insufficient H1 data — skipping Figure 4")
        return

    metrics = ['cycles', 'cache_misses', 'branch_misses']
    metric_labels = ['Cycles', 'L1-dcache load misses', 'Branch misses']
    workload_names = list(groups.keys())
    bar_colors = [COLORS["primary"], COLORS["secondary"], COLORS["accent"]]

    fig, axes = plt.subplots(1, len(metrics), figsize=(4.5 * len(metrics), 5))

    for ax_idx, (ax, metric, mlabel) in enumerate(zip(axes, metrics, metric_labels)):
        means, cis = [], []
        for wl_name in workload_names:
            vals = groups[wl_name][metric].dropna().values.astype(float)
            means.append(np.mean(vals) if len(vals) else 0)
            cis.append(1.96 * np.std(vals, ddof=1) / np.sqrt(len(vals)) if len(vals) > 1 else 0)

        x = np.arange(len(workload_names))
        bars = ax.bar(x, means, 0.6,
                      color=[bar_colors[i % len(bar_colors)] for i in range(len(x))],
                      alpha=0.85, yerr=cis, capsize=5,
                      error_kw={'linewidth': 2})

        ax.set_xticks(x)
        ax.set_xticklabels(workload_names, fontsize=9)
        ax.set_title(mlabel)
        ax.set_ylabel("perf record event count" if ax_idx == 0 else "")
        ax.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: si_label(v))
        )

        for rect, m in zip(bars, means):
            ax.text(rect.get_x() + rect.get_width() / 2, rect.get_height() * 1.02,
                    si_label(m), ha='center', va='bottom', fontsize=9)

    n_desc = ", ".join(
        f"{name.splitlines()[0]} N={groups[name]['cycles'].count()}"
        for name in workload_names
    )
    fig.suptitle(
        "H1: perf record's Absolute Cost Across Workloads\n"
        f"(error bars = 95% CI; {n_desc})",
        fontsize=12
    )
    plt.tight_layout()

    save(fig, "fig4_h1_crossworkload", "Figure 4")


# ---------------------------------------------------------------------------
# Figure 5: Architecture Diagram
# ---------------------------------------------------------------------------

def plot_architecture_diagram(isolcpus):
    """3-layer NUMA isolation architecture diagram.

    Shows the layered isolation design:
      Layer 1 — OS isolation (isolcpus, NUMA awareness)
      Layer 2 — Hardware isolation (CPU pinning per role)
      Layer 3 — Measurement isolation (perf stat as outer ruler)
    """
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5.4)
    ax.axis('off')

    # ---- Layer bands (label at the top of each band, content below) ----
    layers = [
        # (y_bottom, height, title, subtitle, color)
        (0.2, 2.6, "Layer 1: OS Isolation",
         f"isolcpus={isolcpus}  |  NUMA-aware binding", "#DBEAFE"),
        (3.0, 1.0, "Layer 2: Hardware Isolation",
         "One isolated physical core per role  |  ruler on the other socket", "#DCFCE7"),
        (4.2, 1.0, "Layer 3: Measurement Isolation",
         "perf stat counting mode: exact PMU reads, no NMIs, no shared counters", "#FEF9C3"),
    ]
    for y, h, title, subtitle, color in layers:
        ax.add_patch(plt.Rectangle((0.3, y), 9.4, h, linewidth=1.5,
                                   edgecolor='#374151', facecolor=color, zorder=2))
        ax.text(5, y + h - 0.3, title, ha='center', va='center',
                fontsize=11, fontweight='bold', zorder=3)
        ax.text(5, y + h - 0.65, subtitle, ha='center', va='center',
                fontsize=8.5, color='#374151', zorder=3)

    # ---- CPU role boxes (inside Layer 1) ----
    cpu_boxes = [
        # (x_center, label, color)
        (1.6, "CPU 6\nWorkload\n(lat_mem_rd)", "#3B82F6"),
        (3.9, "CPU 7\nperf record\n(tool under study)", "#EF4444"),
        (6.3, "CPU 14\nperf stat\n(outer ruler)", "#10B981"),
        (8.5, "CPU 15\nReserved", "#9CA3AF"),
    ]
    cy = 1.1
    for cx, label, color in cpu_boxes:
        ax.add_patch(plt.Rectangle((cx - 0.85, cy - 0.6), 1.7, 1.2,
                                   linewidth=1.2, edgecolor='white',
                                   facecolor=color, alpha=0.9, zorder=4))
        ax.text(cx, cy, label, ha='center', va='center',
                fontsize=7.5, color='white', fontweight='bold', zorder=5)

    # Socket separator
    ax.plot([5.1, 5.1], [0.3, 1.7], color='#374151', linewidth=1,
            linestyle='--', zorder=3)
    ax.text(2.75, 0.27, "Socket 0", ha='center', va='bottom', fontsize=7.5, color='#374151')
    ax.text(7.4, 0.27, "Socket 1", ha='center', va='bottom', fontsize=7.5, color='#374151')

    # ---- Arrows ----
    arrow_kw = dict(arrowstyle='->', color='#374151', lw=1.5, mutation_scale=15)
    ax.annotate("", xy=(2.45, 1.1), xytext=(3.05, 1.1), arrowprops=arrow_kw, zorder=6)
    ax.text(2.75, 1.78, "samples", ha='center', fontsize=8, color='#374151')
    ax.annotate("", xy=(4.75, 1.1), xytext=(5.45, 1.1), arrowprops=arrow_kw, zorder=6)
    ax.text(5.1, 1.78, "counts", ha='center', fontsize=8, color='#374151')

    ax.set_title(
        "Meta-Profiling: 3-Layer NUMA-Aware Isolation Architecture\n"
        "(Intel Xeon E5-2620 v4)",
        fontsize=12, pad=12
    )

    save(fig, "fig5_architecture", "Figure 5")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global OUTDIR, FIG_EXT
    parser = argparse.ArgumentParser(
        description="Generate publication-quality figures for the Meta-Profiling paper"
    )
    parser.add_argument("dummy_csv", help="CSV from run_hypotheses_dummy.py")
    parser.add_argument("--goapi", type=str, default=None,
                        help="CSV from run_hypotheses_goapi.py")
    parser.add_argument("--outdir", type=str, default="figures",
                        help="Output directory for figures (default: ./figures)")
    parser.add_argument("--format", type=str, default="pdf", choices=["pdf", "png", "svg"],
                        help="Output format (default: pdf)")
    parser.add_argument("--isolcpus", type=str, default="6,7,14,15,22,23,30,31",
                        help="isolcpus list shown in the architecture figure")
    args = parser.parse_args()

    OUTDIR = args.outdir
    FIG_EXT = args.format
    os.makedirs(OUTDIR, exist_ok=True)

    print(f"\n  Loading data...")
    dummy_df = pd.read_csv(args.dummy_csv)
    print(f"  Dummy CSV: {len(dummy_df)} rows")

    goapi_df = None
    if args.goapi and os.path.exists(args.goapi):
        goapi_df = pd.read_csv(args.goapi)
        print(f"  Go API CSV: {len(goapi_df)} rows")

    print(f"\n  Generating figures → {OUTDIR}/\n")

    plot_h2_scaling(dummy_df)
    plot_h3_ablation(dummy_df)
    plot_h4_equivalence(dummy_df, goapi_df)
    plot_h1_crossworkload(dummy_df, goapi_df)
    plot_architecture_diagram(args.isolcpus)

    print(f"\n  ✅ All figures saved to {OUTDIR}/")
    for f in sorted(os.listdir(OUTDIR)):
        if f.endswith(f".{FIG_EXT}"):
            print(f"    {f}")


OUTDIR = "figures"

if __name__ == "__main__":
    main()
