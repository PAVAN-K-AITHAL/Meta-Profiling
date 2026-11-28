#!/usr/bin/env python3
"""
analyze_v2.py — Analysis for the two-sided meta-profiling data

Input: CSVs from run_hypotheses_dummy.py (rows workload_baseline,
tool_overhead, workload_profiled) and, optionally, run_hypotheses_goapi.py.

Per profiled window (paired with its block's baseline):
  slowdown %        100 * (1 - instructions:u(prof) / instructions:u(base))
                    work the workload lost to profiling: the headline number
  time stolen %     100 * (cycles:u(base) - cycles:u(prof)) / cycles:u(base)
                    CPU time taken away from the workload's user code
  IPC loss %        100 * (1 - IPC(prof) / IPC(base))
                    user code running slower: cache/TLB/branch pollution
  kernel cycles     cycles:k(prof) - cycles:k(base), perf's kernel work that
                    the PMU can see
  uncounted cycles  (u+k)(base) - (u+k)(prof): time with all counters
                    disabled, i.e. inside the PMU interrupt handler
  tool % of core    perf record's own cycles / CPU 6 cycles in the window
                    (the only quantity the old method measured)
  per-sample cost   lost user cycles / samples written

Tests:
  H4  one-sample TOST: is slowdown inside ±Δ percentage points (Δ = 1, 0.5)?
  H2  log-log OLS of cost vs samples (exponent b: 1 = linear) and a
      linear-vs-power AIC comparison; flags a cliff where per-sample cost jumps
  H3  paired Wilcoxon signed-rank of each call-graph mode vs no call graph
  H1W per-sample cost vs stack depth (OLS slope = cycles per frame) and
      Kruskal-Wallis across workloads
  Go  paired overhead of task-clock / wall time / cycles, TOST at ±1%
  Holm-Bonferroni over every p-value in the report.

Usage:
  python3 analyze_v2.py results/*.csv --goapi results/goapi.csv \\
      --output report.md --figures figures/
"""

import argparse
import math
import os
import warnings

import numpy as np
import pandas as pd
from scipy import stats

RNG = np.random.default_rng(20261128)
WINDOW_S = 5.0  # set from --window
KEY_BLOCK = ["session_id", "hypothesis", "workload", "iteration"]
KEY_WINDOW = KEY_BLOCK + ["config_g", "config_c"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt(n):
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "—"
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n/div:.2f}{suf}"
    return f"{n:.1f}"


def fp(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "—"
    return f"{p:.2e}" if p < 1e-3 else f"{p:.4f}"


def boot_ci(x, n_boot=5000, level=0.95):
    """Percentile bootstrap CI of the mean. Returns (mean, lo, hi)."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return (math.nan, math.nan, math.nan)
    if len(x) == 1:
        return (x[0], math.nan, math.nan)
    means = RNG.choice(x, size=(n_boot, len(x)), replace=True).mean(axis=1)
    a = (1 - level) / 2
    return (x.mean(), np.quantile(means, a), np.quantile(means, 1 - a))


def ci_str(x, unit="", digits=3):
    m, lo, hi = boot_ci(x)
    if math.isnan(m):
        return "—"
    if math.isnan(lo):
        return f"{m:.{digits}f}{unit}"
    return f"{m:.{digits}f}{unit} [{lo:.{digits}f}, {hi:.{digits}f}]"


def ci_fmt(x):
    m, lo, hi = boot_ci(x)
    if math.isnan(m):
        return "—"
    if math.isnan(lo):
        return fmt(m)
    return f"{fmt(m)} [{fmt(lo)}, {fmt(hi)}]"


def tost_one_sample(x, delta):
    """TOST for H0: |mean| >= delta. Returns (p, ci90_lo, ci90_hi)."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return (math.nan, math.nan, math.nan)
    m, se = x.mean(), x.std(ddof=1) / math.sqrt(n)
    if se == 0:
        return (0.0 if abs(m) < delta else 1.0, m, m)
    p_low = stats.t.sf((m + delta) / se, n - 1)     # H0: mean <= -delta
    p_high = stats.t.cdf((m - delta) / se, n - 1)   # H0: mean >= +delta
    t90 = stats.t.ppf(0.95, n - 1)
    return (max(p_low, p_high), m - t90 * se, m + t90 * se)


class PValues:
    """Collects p-values for one Holm-Bonferroni correction."""

    def __init__(self):
        self.rows = []

    def add(self, test, detail, p):
        if p is not None and not math.isnan(p):
            self.rows.append((test, detail, p))

    def table(self, alpha=0.05):
        if not self.rows:
            return ["No tests."]
        rows = sorted(self.rows, key=lambda r: r[2])
        m = len(rows)
        out = ["| Rank | Test | Detail | Raw p | Holm α | Significant? |",
               "|---|---|---|---|---|---|"]
        still = True
        for i, (test, detail, p) in enumerate(rows):
            adj = alpha / (m - i)
            sig = still and p <= adj
            still = sig
            out.append(f"| {i+1} | {test} | {detail} | {fp(p)} | {adj:.2e} | "
                       f"{'✅' if sig else '❌'} |")
        return out


# ---------------------------------------------------------------------------
# Loading and pairing
# ---------------------------------------------------------------------------

NUMERIC = ["cycles", "cycles_u", "cycles_k", "instructions_u", "cache_misses",
           "branch_misses", "page_faults", "context_switches", "task_clock_ms",
           "duration_ns", "lost_events", "throttle_events", "samples",
           "requests_served", "depth", "size_mb", "config_c", "iteration"]


def load(paths):
    frames = []
    for p in paths:
        df = pd.read_csv(p)
        df["source"] = os.path.basename(p)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    for c in NUMERIC:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["session_id"] = df["session_id"].astype(str)
    return df


def build_windows(df, window_s):
    """One row per profiled window: tool + workload_profiled + baseline."""
    base = df[df.run_type == "workload_baseline"].drop_duplicates(KEY_BLOCK, keep="last")
    prof = df[df.run_type == "workload_profiled"]
    tool = df[df.run_type == "tool_overhead"]
    if prof.empty:
        return pd.DataFrame()

    cols = ["cycles", "cycles_u", "cycles_k", "instructions_u", "cache_misses",
            "context_switches"]
    w = prof[KEY_WINDOW + ["depth", "size_mb", "lost_events", "throttle_events",
                           "samples"] + cols].rename(columns={c: f"p_{c}" for c in cols})
    b = base[KEY_BLOCK + cols].rename(columns={c: f"b_{c}" for c in cols})
    t = tool[KEY_WINDOW + ["cycles", "task_clock_ms", "cache_misses"]].rename(
        columns={"cycles": "t_cycles", "task_clock_ms": "t_task_ms",
                 "cache_misses": "t_cache_misses"})
    w = w.merge(b, on=KEY_BLOCK, how="inner").merge(t, on=KEY_WINDOW, how="left")

    w["invalid"] = (w.lost_events.fillna(0) > 0) | (w.throttle_events.fillna(0) > 0)
    b_tot = w.b_cycles_u + w.b_cycles_k
    p_tot = w.p_cycles_u + w.p_cycles_k
    w["slowdown_pct"] = 100 * (1 - w.p_instructions_u / w.b_instructions_u)
    w["stolen_pct"] = 100 * (w.b_cycles_u - w.p_cycles_u) / w.b_cycles_u
    ipc_b = w.b_instructions_u / w.b_cycles_u
    ipc_p = w.p_instructions_u / w.p_cycles_u
    w["ipc_loss_pct"] = 100 * (1 - ipc_p / ipc_b)
    w["user_lost"] = w.b_cycles_u - w.p_cycles_u
    w["kernel_added"] = w.p_cycles_k - w.b_cycles_k
    w["uncounted"] = b_tot - p_tot
    w["window_cycles"] = b_tot
    w["tool_pct"] = 100 * w.t_cycles / b_tot
    w["tool_core_pct"] = 100 * w.t_task_ms / (window_s * 1000)
    w["l1_increase_pct"] = 100 * (w.p_cache_misses / w.b_cache_misses - 1)
    s = w.samples.where(w.samples > 0)
    w["cyc_per_sample"] = w.user_lost / s
    w["tool_cyc_per_sample"] = w.t_cycles / s
    return w


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def section_quality(w, df):
    out = ["## Data quality", ""]
    if w.empty:
        out.append("No workload_profiled rows found. These CSVs were collected with the "
                   "old method (perf record's PID only); use analyze_hypotheses.py for them.")
        return out
    n_prof = (df.run_type == "workload_profiled").sum()
    n_tool = (df.run_type == "tool_overhead").sum()
    out.append(f"- Profiled windows paired with a baseline: **{len(w)}** "
               f"(workload_profiled rows: {n_prof}, tool_overhead rows: {n_tool})")
    bad = w[w.invalid]
    out.append(f"- Excluded for lost samples or throttling: **{len(bad)}**")
    if len(bad):
        g = bad.groupby(["hypothesis", "workload", "config_g", "config_c"]).size()
        for k, v in g.items():
            out.append(f"  - {k[0]} {k[1]} {k[2]} -c {int(k[3])}: {v}")
    unknown = w.lost_events.isna().sum()
    if unknown:
        out.append(f"- Kept with unreadable quality flags: {unknown}")
    # Conservation check: lost user cycles should equal kernel + uncounted
    gap = (w.user_lost - w.kernel_added - w.uncounted).abs() / w.window_cycles * 100
    out.append(f"- Conservation check |user lost − kernel − uncounted| / window: "
               f"median {gap.median():.4f}% (≈0 by construction; large values mean "
               f"the window lengths differ)")
    base_cv = w.drop_duplicates(KEY_BLOCK).groupby(["hypothesis", "workload"]).b_instructions_u.agg(
        lambda x: x.std() / x.mean() * 100)
    out.append("- Baseline run-to-run variation of instructions:u (CV %):")
    for k, v in base_cv.items():
        out.append(f"  - {k[0]} {k[1]}: {v:.3f}%")
    out.append("")
    return out


def summary_table(g, title):
    out = [f"### {title}", "",
           "| Group | N | Slowdown % | Time stolen % | IPC loss % | Uncounted share of lost | "
           "perf record % of core | Samples | Cycles/sample on CPU 6 | perf record cycles/sample |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for name, x in g:
        unc_share = 100 * x.uncounted / x.user_lost.where(x.user_lost > 0)
        label = name if isinstance(name, str) else " ".join(str(v) for v in name)
        out.append(
            f"| {label} | {len(x)} | {ci_str(x.slowdown_pct)} | {ci_str(x.stolen_pct)} | "
            f"{ci_str(x.ipc_loss_pct)} | {ci_str(unc_share, '%', 1)} | {ci_str(x.tool_core_pct, '', 2)} | "
            f"{fmt(x.samples.mean())} | {ci_fmt(x.cyc_per_sample)} | {ci_fmt(x.tool_cyc_per_sample)} |")
    out.append("")
    out.append("Values are mean [95% bootstrap CI]. *Slowdown* = work lost "
               "(instructions:u). *Time stolen* = user cycles lost. *IPC loss* = user "
               "code running slower (pollution). *Uncounted* = cycles inside the PMU "
               "interrupt handler, where every counter is off.")
    out.append("")
    return out


def section_h1(w, pv, margins):
    x = w[(w.hypothesis == "H1") & ~w.invalid]
    if x.empty:
        return []
    out = ["## H1 / H4 — perf record -g -c 100K", ""]
    out += summary_table(x.groupby("workload"), "Cost to the workload and to the machine")
    out += ["### Decomposition of CPU 6 cycles (mean per 5 s window)", "",
            "| Workload | Window cycles | User cycles lost | Kernel cycles added | Uncounted | "
            "perf record cycles (CPU 7) | Old-method ratio |",
            "|---|---|---|---|---|---|---|"]
    for wl, g in x.groupby("workload"):
        out.append(f"| {wl} | {fmt(g.window_cycles.mean())} | {fmt(g.user_lost.mean())} | "
                   f"{fmt(g.kernel_added.mean())} | {fmt(g.uncounted.mean())} | "
                   f"{fmt(g.t_cycles.mean())} | {g.tool_pct.mean():.3f}% |")
    out.append("")
    out.append("*Old-method ratio* is perf record's PID cycles / workload cycles: "
               "the number earlier drafts reported.")
    out.append("")
    out += ["### H4 — TOST on slowdown (percentage points)", "",
            "| Workload | Mean slowdown | 90% CI | " +
            " | ".join(f"TOST p (Δ={d})" for d in margins) + " |",
            "|---|---|---|" + "---|" * len(margins)]
    for wl, g in x.groupby("workload"):
        cells = []
        ci = None
        for d in margins:
            p, lo, hi = tost_one_sample(g.slowdown_pct, d)
            ci = (lo, hi)
            cells.append(f"{fp(p)} {'✅' if p < 0.05 else '❌'}")
            pv.add("H4 TOST", f"{wl} slowdown Δ={d}pp", p)
        out.append(f"| {wl} | {g.slowdown_pct.mean():.3f}% | [{ci[0]:.3f}, {ci[1]:.3f}] | "
                   + " | ".join(cells) + " |")
    out.append("")
    return out


def fit_power(xs, ys):
    """OLS of log y on log x.
    Returns (b, b_lo, b_hi, r2, n, stderr) or None."""
    m = (xs > 0) & (ys > 0) & np.isfinite(xs) & np.isfinite(ys)
    n = int(m.sum())
    if n < 4 or len(np.unique(xs[m])) < 3:
        return None
    r = stats.linregress(np.log(xs[m]), np.log(ys[m]))
    t = stats.t.ppf(0.975, n - 2)
    return (r.slope, r.slope - t * r.stderr, r.slope + t * r.stderr, r.rvalue ** 2, n, r.stderr)


def aic_linear_vs_power(xs, ys):
    """AIC (Gaussian errors on y) for y = a + k x vs y = A x^b."""
    m = (xs > 0) & (ys > 0) & np.isfinite(xs) & np.isfinite(ys)
    x, y = xs[m], ys[m]
    n = len(x)
    if n < 5:
        return None
    lin = stats.linregress(x, y)
    rss_lin = ((y - (lin.intercept + lin.slope * x)) ** 2).sum()
    pw = stats.linregress(np.log(x), np.log(y))
    rss_pw = ((y - np.exp(pw.intercept) * x ** pw.slope) ** 2).sum()
    aic = lambda rss: n * math.log(rss / n) + 2 * 3
    return aic(rss_lin), aic(rss_pw)


def section_h2(w, pv):
    x = w[w.hypothesis == "H2"]
    if x.empty:
        return []
    out = ["## H2 — Sampling period sweep (-g)", ""]
    valid = x[~x.invalid]
    rows = []
    for c, g in sorted(x.groupby("config_c"), key=lambda kv: -kv[0]):
        gv = g[~g.invalid]
        rows.append((int(c), len(g), len(gv), gv))
    out += ["| -c | Runs (valid) | Samples/s | Slowdown % | perf record % of core | "
            "Cycles/sample on CPU 6 | perf record cycles/sample |",
            "|---|---|---|---|---|---|---|"]
    for c, n, nv, gv in rows:
        sps = gv.samples.mean() / WINDOW_S if nv else math.nan
        out.append(f"| {fmt(c)} | {n} ({nv}) | {fmt(sps)} | {ci_str(gv.slowdown_pct)} | "
                   f"{ci_str(gv.tool_core_pct, '', 2)} | {ci_fmt(gv.cyc_per_sample)} | "
                   f"{ci_fmt(gv.tool_cyc_per_sample)} |")
    out.append("")

    # Cliff: perf record saturates its core (>= 50% of CPU 7) or drops
    # samples in at least 20% of runs — it no longer delivers the requested rate
    cliff = []
    for c, n, nv, gv in rows:
        saturated = nv and gv.tool_core_pct.median() >= 50
        dropping = n and (n - nv) / n >= 0.2
        if saturated or dropping:
            cliff.append((c, "saturated" if saturated else f"{n - nv}/{n} runs dropped samples"))
    if cliff:
        out.append("⚠️ **Cliff:** " + "; ".join(f"-c {fmt(c)} ({why})" for c, why in cliff) +
                   ". perf record can no longer keep up, so these periods are excluded "
                   "from the fits below.")
        out.append("")
        valid = valid[~valid.config_c.isin([c for c, _ in cliff])]

    out += ["### Scaling fits (valid runs, cliff excluded)", "",
            "| Cost | Exponent b [95% CI] | R² (log-log) | AIC linear | AIC power | Reading |",
            "|---|---|---|---|---|---|"]
    for name, col in (("Slowdown % (CPU 6)", "slowdown_pct"),
                      ("perf record cycles (CPU 7)", "t_cycles")):
        xs = valid.samples.to_numpy(float)
        ys = valid[col].to_numpy(float)
        f = fit_power(xs, ys)
        a = aic_linear_vs_power(xs, ys)
        if not f:
            out.append(f"| {name} | — | — | — | — | not enough distinct periods |")
            continue
        b, lo, hi, r2, n, se = f
        # Practical band: within ±5% of proportional counts as linear
        reading = ("linear (CI contains 1)" if lo <= 1 <= hi else
                   "≈ linear (CI inside [0.95, 1.05])" if 0.95 <= lo and hi <= 1.05 else
                   "sub-linear" if hi < 1 else "super-linear")
        out.append(f"| {name} | {b:.3f} [{lo:.3f}, {hi:.3f}] | {r2:.4f} | "
                   f"{a[0]:.1f} | {a[1]:.1f} | {reading} |" if a else
                   f"| {name} | {b:.3f} [{lo:.3f}, {hi:.3f}] | {r2:.4f} | — | — | {reading} |")
        if se > 0:
            pv.add("H2 exponent = 1", name, 2 * stats.t.sf(abs((b - 1) / se), n - 2))
    out.append("")
    out.append("b = 1 means cost is proportional to the number of samples; "
               "lower AIC is the better model.")
    out.append("")
    return out


def section_h3(w, pv):
    x = w[(w.hypothesis == "H3") & ~w.invalid]
    if x.empty:
        return []
    out = ["## H3 — Call-graph mode (-c 100K)", ""]
    for wl, xw in x.groupby("workload"):
        out += summary_table(xw.groupby("config_g"), f"{wl}")
        ref = xw[xw.config_g == "off"].set_index(["session_id", "iteration"])
        out += [f"Paired vs no call graph ({wl}):", "",
                "| Mode | Pairs | Δ slowdown (pp) | Δ perf record cycles | Wilcoxon p (slowdown) | "
                "Wilcoxon p (perf record cycles) |",
                "|---|---|---|---|---|---|"]
        for mode, g in xw[xw.config_g != "off"].groupby("config_g"):
            j = g.set_index(["session_id", "iteration"]).join(ref, rsuffix="_ref", how="inner")
            if len(j) < 5:
                continue
            d_slow = j.slowdown_pct - j.slowdown_pct_ref
            d_tool = j.t_cycles - j.t_cycles_ref
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p1 = stats.wilcoxon(d_slow.dropna()).pvalue
                p2 = stats.wilcoxon(d_tool.dropna()).pvalue
            pv.add("H3 Wilcoxon", f"{wl} {mode} vs off slowdown", p1)
            pv.add("H3 Wilcoxon", f"{wl} {mode} vs off perf record cycles", p2)
            out.append(f"| {mode} | {len(j)} | {ci_str(d_slow)} | {ci_fmt(d_tool)} | "
                       f"{fp(p1)} | {fp(p2)} |")
        out.append("")
    return out


def section_h1w(w, pv):
    x = w[(w.hypothesis == "H1W") & ~w.invalid]
    if x.empty:
        return []
    out = ["## H1W — Does the cost depend on the workload?", ""]
    out += summary_table(x.groupby("workload"), "Per workload (-g -c 100K)")

    groups = [g.cyc_per_sample.dropna().to_numpy() for _, g in x.groupby("workload")]
    groups = [g for g in groups if len(g) >= 3]
    if len(groups) >= 2:
        h, p = stats.kruskal(*groups)
        pv.add("H1W Kruskal-Wallis", "cycles/sample across workloads", p)
        out.append(f"Kruskal-Wallis on cycles/sample across workloads: H = {h:.1f}, p = {fp(p)} "
                   f"({'cost differs between workloads' if p < 0.05 else 'no detectable difference'}).")
        out.append("")

    for size in sorted(x.size_mb.dropna().unique()):
        s = x[(x.size_mb == size) & x.depth.notna()]
        if s.depth.nunique() < 3:
            continue
        r = stats.linregress(s.depth, s.cyc_per_sample)
        t = stats.t.ppf(0.975, len(s) - 2)
        pv.add("H1W depth slope", f"{int(size)} MB cycles/sample vs depth", r.pvalue)
        out.append(f"**Stack depth, {int(size)} MB working set:** cycles/sample = "
                   f"{r.intercept:.0f} + {r.slope:.1f} × depth "
                   f"(95% CI of slope [{r.slope - t*r.stderr:.1f}, {r.slope + t*r.stderr:.1f}], "
                   f"R² = {r.rvalue**2:.3f}, p = {fp(r.pvalue)}). The slope is the cost of "
                   f"unwinding one frame.")
        rt = stats.linregress(s.depth, s.tool_cyc_per_sample)
        out.append(f"perf record's own cycles/sample vs depth: slope {rt.slope:.1f} "
                   f"(R² = {rt.rvalue**2:.3f}): bigger callchains also cost the tool more to write.")
        out.append("")
    return out


def section_goapi(paths, pv, margins):
    if not paths:
        return []
    g = load(paths)
    out = ["## Go API — constant work (1,000 requests per run)", ""]
    base = g[(g.hypothesis == "H4") & (g.run_type == "baseline")]
    prof = g[(g.hypothesis == "H4") & (g.run_type == "profiled")]
    if base.empty or prof.empty:
        out.append("No H4 baseline/profiled pairs.")
    else:
        j = prof.merge(base, on=["session_id", "iteration"], suffixes=("", "_b"))
        j["invalid"] = (j.lost_events.fillna(0) > 0) | (j.throttle_events.fillna(0) > 0)
        out.append(f"Excluded for lost samples or throttling: {int(j.invalid.sum())} of {len(j)}")
        out.append("")
        j = j[~j.invalid]
        out += ["| -c | Pairs | CPU time overhead % | Wall time overhead % | Cycles overhead % | " +
                " | ".join(f"TOST p CPU time (Δ={d}%)" for d in margins) + " |",
                "|---|---|---|---|---|" + "---|" * len(margins)]
        for c, x in sorted(j.groupby("config_c"), key=lambda kv: -kv[0]):
            cells = {}
            for name, col in (("cpu", "task_clock_ms"), ("wall", "duration_ns"), ("cyc", "cycles")):
                if col in x and x[col].notna().any():
                    cells[name] = 100 * (x[col] / x[f"{col}_b"] - 1)
                else:
                    cells[name] = pd.Series(dtype=float)
            tost_cells = []
            for d in margins:
                p, _, _ = tost_one_sample(cells["cpu"], d)
                pv.add("Go API TOST", f"-c {int(c)} CPU time Δ={d}%", p)
                tost_cells.append(f"{fp(p)} {'✅' if p < 0.05 else '❌'}")
            out.append(f"| {fmt(c)} | {len(x)} | {ci_str(cells['cpu'])} | {ci_str(cells['wall'])} | "
                       f"{ci_str(cells['cyc'])} | " + " | ".join(tost_cells) + " |")
        out.append("")
        out.append("CPU time = task-clock of the API's threads; it includes time in the PMU "
                   "interrupt handler, which *cycles* cannot see. A gap between the CPU-time "
                   "and cycles columns is that blind spot.")
        out.append("")

    t = g[(g.hypothesis == "H1") & (g.run_type == "tool_overhead")]
    if not t.empty:
        out.append(f"perf record's own process over 1,000 requests: {ci_fmt(t.cycles)} cycles, "
                   f"{ci_str(t.task_clock_ms, ' ms', 1)} CPU time (N = {len(t)}).")
        out.append("")
    return out


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def figures(w, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(outdir, exist_ok=True)
    made = []

    x = w[(w.hypothesis == "H2") & ~w.invalid]
    if not x.empty:
        g = x.groupby("config_c").agg(samples=("samples", "mean"),
                                      slow=("slowdown_pct", "mean"),
                                      slow_sd=("slowdown_pct", "std"),
                                      tool=("tool_core_pct", "mean"),
                                      tool_sd=("tool_core_pct", "std")).sort_values("samples")
        fig, ax = plt.subplots(figsize=(4.2, 3.2))
        ax.errorbar(g.samples / WINDOW_S, g.slow, yerr=g.slow_sd, marker="o", capsize=3,
                    label="Workload slowdown (CPU 6)")
        ax.errorbar(g.samples / WINDOW_S, g.tool, yerr=g.tool_sd, marker="s", capsize=3,
                    label="perf record process (CPU 7, % of core)")
        for c, r in g.iterrows():
            ax.annotate(f"-c {fmt(c)}", (r.samples / WINDOW_S, r.slow), textcoords="offset points",
                        xytext=(4, -10), fontsize=6)
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlabel("Samples per second"); ax.set_ylabel("Cost (%)")
        ax.legend(fontsize=7); ax.grid(True, which="both", alpha=0.3)
        fig.tight_layout(); p = os.path.join(outdir, "fig_h2_scaling.pdf")
        fig.savefig(p); plt.close(fig); made.append(p)

    x = w[(w.hypothesis == "H3") & ~w.invalid]
    if not x.empty:
        order = [m for m in ("off", "on", "lbr", "dwarf") if m in set(x.config_g)]
        names = {"off": "none", "on": "frame ptr", "lbr": "LBR", "dwarf": "DWARF"}
        wls = sorted(x.workload.unique())
        fig, ax = plt.subplots(figsize=(4.2, 3.2))
        width = 0.8 / len(wls)
        for i, wl in enumerate(wls):
            means = [x[(x.workload == wl) & (x.config_g == m)].slowdown_pct.mean() for m in order]
            sds = [x[(x.workload == wl) & (x.config_g == m)].slowdown_pct.std() for m in order]
            ax.bar(np.arange(len(order)) + i * width, means, width, yerr=sds, capsize=3, label=wl)
        ax.set_xticks(np.arange(len(order)) + width * (len(wls) - 1) / 2)
        ax.set_xticklabels([names[m] for m in order])
        ax.set_ylabel("Workload slowdown (%)"); ax.set_xlabel("Call-graph mode (-c 100K)")
        ax.legend(fontsize=7); ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout(); p = os.path.join(outdir, "fig_h3_modes.pdf")
        fig.savefig(p); plt.close(fig); made.append(p)

    x = w[(w.hypothesis == "H1W") & ~w.invalid & w.depth.notna()]
    if not x.empty:
        fig, ax = plt.subplots(figsize=(4.2, 3.2))
        for size, s in x.groupby("size_mb"):
            g = s.groupby("depth").cyc_per_sample.agg(["mean", "std"])
            ax.errorbar(g.index, g["mean"], yerr=g["std"], marker="o", capsize=3,
                        label=f"{int(size)} MB working set")
        ax.set_xlabel("Call-stack depth (frames)")
        ax.set_ylabel("Lost workload cycles per sample")
        ax.legend(fontsize=7); ax.grid(True, alpha=0.3)
        fig.tight_layout(); p = os.path.join(outdir, "fig_h1w_depth.pdf")
        fig.savefig(p); plt.close(fig); made.append(p)
    return made


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("csv", nargs="*", help="CSV(s) from run_hypotheses_dummy.py")
    ap.add_argument("--goapi", nargs="*", default=[], help="CSV(s) from run_hypotheses_goapi.py")
    ap.add_argument("--output", help="Write the report here (default: print)")
    ap.add_argument("--figures", help="Directory for PDF figures")
    ap.add_argument("--window", type=float, default=5.0,
                    help="Measurement window in seconds (default: 5)")
    ap.add_argument("--margins", default="1,0.5",
                    help="TOST margins in percentage points (default: 1,0.5)")
    args = ap.parse_args()
    margins = [float(m) for m in args.margins.split(",")]
    global WINDOW_S
    WINDOW_S = args.window

    pv = PValues()
    out = ["# Meta-profiling results (two-sided measurement)", ""]
    if args.csv:
        df = load(args.csv)
        out.append("Sources: " + ", ".join(f"`{os.path.basename(p)}`" for p in args.csv))
        out.append("")
        w = build_windows(df, args.window)
        out += section_quality(w, df)
        if not w.empty:
            out += section_h1(w, pv, margins)
            out += section_h2(w, pv)
            out += section_h3(w, pv)
            out += section_h1w(w, pv)
            if args.figures:
                made = figures(w, args.figures)
                out += ["## Figures", ""] + [f"- `{p}`" for p in made] + [""]
    out += section_goapi(args.goapi, pv, margins)
    out += ["## Holm-Bonferroni correction (all tests above)", ""] + pv.table() + [""]

    text = "\n".join(out)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print(f"Report written to {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
