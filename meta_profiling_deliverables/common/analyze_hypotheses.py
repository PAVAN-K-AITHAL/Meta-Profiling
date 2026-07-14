#!/usr/bin/env python3
"""
analyze_hypotheses.py — Statistical Analysis for All 4 Hypotheses

Reads the unified CSV output from run_hypotheses_dummy.py and
run_hypotheses_goapi.py, then performs the formal statistical tests
defined in hypothesis_framework.md.

H1: Constant Absolute Cost → Welch's ANOVA + TOST equivalence
H2: Linear Scaling          → OLS regression + quadratic F-test
H3: -g Ablation             → Paired t-test (or Wilcoxon) + Cohen's d
H4: Production Negligibility → TOST equivalence with Δ = 1%

Cross-cutting: Holm-Bonferroni correction across all tests.

Usage:
  python3 analyze_hypotheses.py <dummy_csv>
  python3 analyze_hypotheses.py <dummy_csv> --goapi <goapi_csv>
  python3 analyze_hypotheses.py <dummy_csv> --goapi <goapi_csv> --output report.md
"""

import argparse
import math
import warnings
import os
from collections import defaultdict

import pandas as pd
import numpy as np
from scipy import stats

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt(n):
    """Format large numbers with SI suffixes."""
    n = float(n)
    if abs(n) >= 1e9:
        return f"{n/1e9:.2f}B"
    elif abs(n) >= 1e6:
        return f"{n/1e6:.2f}M"
    elif abs(n) >= 1e3:
        return f"{n/1e3:.2f}K"
    else:
        return f"{n:.1f}"


def cohens_d(g1, g2):
    """Cohen's d effect size (pooled std)."""
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return 0.0
    m1, m2 = np.mean(g1), np.mean(g2)
    v1, v2 = np.var(g1, ddof=1), np.var(g2, ddof=1)
    pooled = math.sqrt(((n1-1)*v1 + (n2-1)*v2) / (n1+n2-2))
    if pooled == 0:
        return float('inf') if m1 != m2 else 0.0
    return abs(m1 - m2) / pooled


def tost_test(g1, g2, delta_frac):
    """Two One-Sided Tests (TOST) for equivalence.
    
    H0: |μ1 - μ2| >= Δ  (NOT equivalent)
    H1: |μ1 - μ2| < Δ   (equivalent)
    
    delta_frac: equivalence bound as a fraction of g1 mean (e.g. 0.01 = 1%)
    
    Returns: (tost_p, lower_t, upper_t, ci_low, ci_high, delta)
    """
    m1, m2 = np.mean(g1), np.mean(g2)
    delta = abs(m1) * delta_frac

    diff = m2 - m1
    se = math.sqrt(np.var(g1, ddof=1)/len(g1) + np.var(g2, ddof=1)/len(g2))

    if se == 0:
        if abs(diff) < delta:
            return 0.001, 0, 0, diff, diff, delta  # trivially equivalent
        else:
            return 1.0, 0, 0, diff, diff, delta

    # Welch-Satterthwaite degrees of freedom
    v1, v2 = np.var(g1, ddof=1), np.var(g2, ddof=1)
    n1, n2 = len(g1), len(g2)
    num = (v1/n1 + v2/n2)**2
    den = (v1/n1)**2/(n1-1) + (v2/n2)**2/(n2-1)
    df = num / den if den > 0 else min(n1, n2) - 1

    # Two one-sided tests
    t_lower = (diff - (-delta)) / se
    t_upper = (diff - delta) / se

    p_lower = 1 - stats.t.cdf(t_lower, df)  # test diff > -delta
    p_upper = stats.t.cdf(t_upper, df)       # test diff < +delta

    tost_p = max(p_lower, p_upper)

    # 90% CI for the difference (corresponds to two one-sided α=0.05)
    t_crit = stats.t.ppf(0.95, df)
    ci_low = diff - t_crit * se
    ci_high = diff + t_crit * se

    return tost_p, t_lower, t_upper, ci_low, ci_high, delta


def normality_check(data, alpha=0.05):
    """Shapiro-Wilk test. Returns (is_normal, p_value)."""
    if len(data) < 3:
        return True, 1.0
    # Shapiro-Wilk works best with n ≤ 5000
    sample = data[:5000] if len(data) > 5000 else data
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, p = stats.shapiro(sample)
    return p > alpha, p


# ---------------------------------------------------------------------------
# H1 Analysis: Constant Absolute Cost
# ---------------------------------------------------------------------------

def analyze_h1(dummy_df, goapi_df=None):
    """H1: Is perf record's overhead constant across workloads?
    
    Overhead = profiled_workload_counters - baseline_workload_counters.
    Test: Welch's ANOVA across workload groups
    Equivalence: TOST with Δ = 10% of grand mean
    """
    results = []
    report_lines = []

    report_lines.append("## H1: Constant Absolute Cost")
    report_lines.append("")
    report_lines.append("> **H₀:** The absolute hardware cost of `perf record -g -c 100K` is the")
    report_lines.append("> same regardless of the target workload (within an equivalence margin).")
    report_lines.append("")

    h1_data = dummy_df[dummy_df['hypothesis'] == 'H1']
    if h1_data.empty:
        report_lines.append("⚠️ No H1 data found in dummy CSV.\n")
        return results, report_lines

    metrics = ['cycles', 'cache_misses', 'branch_misses', 'context_switches']

    # Compute per-iteration overhead for the dummy workload
    baseline_dummy = h1_data[h1_data['run_type'] == 'baseline']
    profiled_dummy = h1_data[h1_data['run_type'] == 'profiled']

    # Also check for legacy 'tool_overhead' run_type
    if baseline_dummy.empty and profiled_dummy.empty:
        tool_overhead = h1_data[h1_data['run_type'] == 'tool_overhead']
        if not tool_overhead.empty:
            # Legacy format — use raw values directly
            groups = {'dummy': tool_overhead}
            if goapi_df is not None:
                h1_goapi = goapi_df[goapi_df['hypothesis'] == 'H1']
                goapi_tool = h1_goapi[h1_goapi['run_type'] == 'tool_overhead']
                if not goapi_tool.empty:
                    groups['goapi'] = goapi_tool
        else:
            report_lines.append("⚠️ No baseline/profiled data found for H1.\n")
            return results, report_lines
    else:
        # New format: compute overhead = profiled - baseline per iteration
        groups = {}
        
        # Dummy workload overhead
        dummy_overheads = {}
        for metric in metrics:
            b_vals = baseline_dummy.groupby('iteration')[metric].mean()
            p_vals = profiled_dummy.groupby('iteration')[metric].mean()
            common_iters = b_vals.index.intersection(p_vals.index)
            if len(common_iters) > 0:
                overhead_series = p_vals.loc[common_iters] - b_vals.loc[common_iters]
                dummy_overheads[metric] = overhead_series.values
        if dummy_overheads:
            groups['dummy'] = dummy_overheads

        # Go API overhead (if provided)
        if goapi_df is not None:
            h1_goapi = goapi_df[goapi_df['hypothesis'] == 'H1']
            if not h1_goapi.empty:
                goapi_baseline = h1_goapi[h1_goapi['run_type'] == 'baseline']
                goapi_profiled = h1_goapi[h1_goapi['run_type'] == 'profiled']
                goapi_overheads = {}
                for metric in metrics:
                    b_vals = goapi_baseline.groupby('iteration')[metric].mean()
                    p_vals = goapi_profiled.groupby('iteration')[metric].mean()
                    common_iters = b_vals.index.intersection(p_vals.index)
                    if len(common_iters) > 0:
                        overhead_series = p_vals.loc[common_iters] - b_vals.loc[common_iters]
                        goapi_overheads[metric] = overhead_series.values
                if goapi_overheads:
                    groups['goapi'] = goapi_overheads

    # Report: descriptive stats + overhead comparison
    report_lines.append("### Baseline vs Profiled (Workload Perturbation)")
    report_lines.append("")
    report_lines.append("| Metric | Baseline Mean | Profiled Mean | Overhead | Overhead % |")
    report_lines.append("|--------|-------------|--------------|----------|-----------|")

    for metric in metrics:
        b_vals = baseline_dummy[metric].dropna().values.astype(float)
        p_vals = profiled_dummy[metric].dropna().values.astype(float)
        if len(b_vals) > 0 and len(p_vals) > 0:
            b_mean = np.mean(b_vals)
            p_mean = np.mean(p_vals)
            overhead = p_mean - b_mean
            overhead_pct = (overhead / b_mean * 100) if b_mean > 0 else 0
            report_lines.append(
                f"| {metric} | {fmt(b_mean)} | {fmt(p_mean)} | "
                f"{fmt(abs(overhead))} | {overhead_pct:+.4f}% |"
            )
    report_lines.append("")

    # Cross-workload comparison (if we have multiple groups)
    if len(groups) >= 2 and isinstance(list(groups.values())[0], dict):
        report_lines.append("### Cross-Workload Overhead Comparison")
        report_lines.append("")
        report_lines.append("| Metric | Group | N | Mean Overhead | Std | Cohen's d | ANOVA p | TOST p | Equivalent? |")
        report_lines.append("|--------|-------|---|--------------|-----|-----------|---------|--------|-------------|")

        for metric in metrics:
            group_data = {}
            for name, data_dict in groups.items():
                if metric in data_dict:
                    vals = data_dict[metric]
                    if len(vals) > 0:
                        group_data[name] = vals

            if len(group_data) < 2:
                for name, vals in group_data.items():
                    report_lines.append(
                        f"| {metric} | {name} | {len(vals)} | {fmt(np.mean(vals))} | "
                        f"{fmt(np.std(vals, ddof=1))} | — | — | — | Single group |"
                    )
                continue

            all_groups = list(group_data.values())
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if len(all_groups) == 2:
                    t_stat, anova_p = stats.ttest_ind(all_groups[0], all_groups[1], equal_var=False)
                else:
                    anova_p = stats.f_oneway(*all_groups).pvalue

            if len(all_groups) == 2:
                tost_p, _, _, ci_lo, ci_hi, delta = tost_test(
                    all_groups[0], all_groups[1], 0.10
                )
                d = cohens_d(all_groups[0], all_groups[1])
                equiv = "✅ YES" if tost_p < 0.05 else "❌ NO"
            else:
                tost_p = float('nan')
                d = 0
                equiv = "—"

            results.append({"test": "H1", "metric": metric, "p_value": anova_p, "test_type": "ANOVA"})
            results.append({"test": "H1", "metric": metric, "p_value": tost_p, "test_type": "TOST"})

            for name, vals in group_data.items():
                report_lines.append(
                    f"| {metric} | {name} | {len(vals)} | {fmt(np.mean(vals))} | "
                    f"{fmt(np.std(vals, ddof=1))} | {d:.2f} | {anova_p:.4e} | "
                    f"{tost_p:.4e} | {equiv} |"
                )
    elif len(groups) == 1:
        report_lines.append("> ℹ️ Only one workload group available. Run Go API experiments")
        report_lines.append("> to enable cross-workload comparison (H1 requires ≥2 groups).")

    report_lines.append("")
    return results, report_lines


# ---------------------------------------------------------------------------
# H2 Analysis: Linear Scaling
# ---------------------------------------------------------------------------

def analyze_h2(dummy_df):
    """H2: Does overhead scale linearly with 1/c (sampling frequency)?
    
    Test: OLS regression + quadratic F-test for non-linearity
    """
    results = []
    report_lines = []

    report_lines.append("## H2: Linear Scaling with Sampling Frequency")
    report_lines.append("")
    report_lines.append("> **H₀:** The overhead scales linearly with sampling frequency (1/c).")
    report_lines.append("> **H₁:** The relationship is non-linear (super-linear or sub-linear).")
    report_lines.append("")

    h2_data = dummy_df[dummy_df['hypothesis'] == 'H2']
    if h2_data.empty:
        report_lines.append("⚠️ No H2 data found.\n")
        return results, report_lines

    # Use only profiled rows (or tool_overhead for legacy format)
    h2_profiled = h2_data[h2_data['run_type'].isin(['profiled', 'tool_overhead'])]
    if h2_profiled.empty:
        h2_profiled = h2_data  # fallback: use all rows

    metrics = ['cycles', 'cache_misses', 'branch_misses']

    for metric in metrics:
        report_lines.append(f"### {metric}")
        report_lines.append("")

        # Group by sampling period
        grouped = h2_profiled.groupby('config_c')[metric].apply(list).to_dict()

        # Build regression data
        x_vals = []  # sampling frequency = 1/c
        y_vals = []  # overhead
        for c_val, values in sorted(grouped.items()):
            freq = 1.0 / float(c_val)
            for v in values:
                x_vals.append(freq)
                y_vals.append(float(v))

        x = np.array(x_vals)
        y = np.array(y_vals)

        if len(x) < 6:
            report_lines.append("⚠️ Not enough data points for regression.\n")
            continue

        # ---- Linear OLS ----
        slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
        r_sq = r_value ** 2

        report_lines.append(f"**Linear Fit:** y = {slope:.2e}·x + {intercept:.2e}")
        report_lines.append(f"  R² = {r_sq:.4f}, slope p = {p_value:.4e}")
        report_lines.append("")

        # ---- Quadratic fit + F-test for non-linearity ----
        # Fit: y = a·x² + b·x + c
        coeffs = np.polyfit(x, y, 2)
        y_pred_linear = slope * x + intercept
        y_pred_quad = np.polyval(coeffs, x)

        ss_res_linear = np.sum((y - y_pred_linear) ** 2)
        ss_res_quad = np.sum((y - y_pred_quad) ** 2)
        n = len(y)

        # F-test: does the quadratic term significantly improve fit?
        # F = ((SS_linear - SS_quad) / 1) / (SS_quad / (n - 3))
        if ss_res_quad > 0 and n > 3:
            f_stat = ((ss_res_linear - ss_res_quad) / 1) / (ss_res_quad / (n - 3))
            f_p = 1 - stats.f.cdf(f_stat, 1, n - 3)
        else:
            f_stat = 0
            f_p = 1.0

        nonlinear = "YES (non-linear)" if f_p < 0.05 else "NO (linear holds)"

        report_lines.append(f"**Non-linearity F-test:** F = {f_stat:.2f}, p = {f_p:.4e}")
        report_lines.append(f"  Non-linear? {nonlinear}")
        report_lines.append("")

        # Per-config summary table
        report_lines.append("| -c Value | N | Mean | Std |")
        report_lines.append("|----------|---|------|-----|")
        for c_val in sorted(grouped.keys()):
            vals = np.array(grouped[c_val], dtype=float)
            report_lines.append(
                f"| {fmt(float(c_val))} | {len(vals)} | {fmt(np.mean(vals))} | "
                f"{fmt(np.std(vals, ddof=1))} |"
            )
        report_lines.append("")

        results.append({"test": "H2", "metric": metric, "p_value": p_value, "test_type": "OLS_slope"})
        results.append({"test": "H2", "metric": metric, "p_value": f_p, "test_type": "nonlinearity_F"})

    return results, report_lines


# ---------------------------------------------------------------------------
# H3 Analysis: -g Ablation
# ---------------------------------------------------------------------------

def analyze_h3(dummy_df):
    """H3: Does -g (call graph) significantly increase overhead?
    
    Test: Paired t-test (or Wilcoxon) + Cohen's d
    """
    results = []
    report_lines = []

    report_lines.append("## H3: Call-Graph (`-g`) Ablation")
    report_lines.append("")
    report_lines.append("> **H₀:** Removing `-g` does NOT reduce overhead.")
    report_lines.append("> **H₁:** Removing `-g` significantly reduces overhead.")
    report_lines.append("")

    h3_data = dummy_df[dummy_df['hypothesis'] == 'H3']
    if h3_data.empty:
        report_lines.append("⚠️ No H3 data found.\n")
        return results, report_lines

    # Use only profiled rows (or tool_overhead for legacy format)
    h3_profiled = h3_data[h3_data['run_type'].isin(['profiled', 'tool_overhead'])]
    if h3_profiled.empty:
        h3_profiled = h3_data  # fallback

    with_g = h3_profiled[h3_profiled['config_g'] == 'on']
    without_g = h3_profiled[h3_profiled['config_g'] == 'off']

    metrics = ['cycles', 'cache_misses', 'branch_misses', 'context_switches']

    report_lines.append("| Metric | With -g (mean) | Without -g (mean) | Diff | Diff % | p-value | Cohen's d | Significant? |")
    report_lines.append("|--------|---------------|------------------|------|--------|---------|-----------|-------------|")

    for metric in metrics:
        g_on = with_g[metric].dropna().values.astype(float)
        g_off = without_g[metric].dropna().values.astype(float)

        if len(g_on) < 2 or len(g_off) < 2:
            continue

        m_on = np.mean(g_on)
        m_off = np.mean(g_off)
        diff = m_on - m_off
        diff_pct = (diff / m_off * 100) if m_off > 0 else 0

        # Check normality for test selection
        norm_on, _ = normality_check(g_on)
        norm_off, _ = normality_check(g_off)

        if norm_on and norm_off:
            # Use min-length paired design if possible
            n_min = min(len(g_on), len(g_off))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                t_stat, p_val = stats.ttest_ind(g_on, g_off, equal_var=False)
            test_used = "Welch's t"
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                stat, p_val = stats.mannwhitneyu(g_on, g_off, alternative='two-sided')
            test_used = "Mann-Whitney U"

        d = cohens_d(g_on, g_off)
        sig = "✅ YES" if p_val < 0.05 else "❌ NO"

        report_lines.append(
            f"| {metric} | {fmt(m_on)} | {fmt(m_off)} | "
            f"{fmt(abs(diff))} | {diff_pct:+.2f}% | {p_val:.4e} | "
            f"{d:.2f} | {sig} |"
        )

        results.append({"test": "H3", "metric": metric, "p_value": p_val, "test_type": test_used})

    # Attribution
    g_on_cycles = with_g['cycles'].dropna().values.astype(float)
    g_off_cycles = without_g['cycles'].dropna().values.astype(float)
    if len(g_on_cycles) > 0 and len(g_off_cycles) > 0:
        total = np.mean(g_on_cycles)
        g_cost = np.mean(g_on_cycles) - np.mean(g_off_cycles)
        if total > 0:
            attribution = g_cost / total * 100
            report_lines.append("")
            report_lines.append(
                f"> **Attribution:** Call-graph unwinding (`-g`) accounts for "
                f"~{attribution:.1f}% of perf record's total cycle overhead."
            )

    report_lines.append("")
    return results, report_lines


# ---------------------------------------------------------------------------
# H4 Analysis: Production Negligibility
# ---------------------------------------------------------------------------

def analyze_h4(goapi_df):
    """H4: Is total overhead < 1% of Go API resource consumption?
    
    Test: TOST equivalence with Δ = 1% of baseline mean
    """
    results = []
    report_lines = []

    report_lines.append("## H4: Production Negligibility (< 1% Overhead)")
    report_lines.append("")
    report_lines.append("> **H₀:** Profiler overhead ≥ 1% of Go API resource consumption.")
    report_lines.append("> **H₁:** Profiler overhead < 1% (negligible for production).")
    report_lines.append("")

    if goapi_df is None:
        report_lines.append("⚠️ No Go API data provided. Run run_hypotheses_goapi.py first.\n")
        return results, report_lines

    h4_data = goapi_df[goapi_df['hypothesis'] == 'H4']
    if h4_data.empty:
        report_lines.append("⚠️ No H4 data found in Go API CSV.\n")
        return results, report_lines

    baseline = h4_data[h4_data['run_type'] == 'baseline']
    profiled = h4_data[h4_data['run_type'] == 'profiled']

    metrics = ['cycles', 'cache_misses', 'branch_misses', 'context_switches']

    report_lines.append("| Metric | Baseline Mean | Profiled Mean | Overhead | Overhead % | TOST p | 90% CI | < 1%? |")
    report_lines.append("|--------|-------------|--------------|----------|-----------|--------|--------|-------|")

    for metric in metrics:
        b_vals = baseline[metric].dropna().values.astype(float)
        p_vals = profiled[metric].dropna().values.astype(float)

        if len(b_vals) < 2 or len(p_vals) < 2:
            continue

        b_mean = np.mean(b_vals)
        p_mean = np.mean(p_vals)
        overhead = p_mean - b_mean
        overhead_pct = (overhead / b_mean * 100) if b_mean > 0 else 0

        # TOST with Δ = 1% of baseline
        tost_p, _, _, ci_lo, ci_hi, delta = tost_test(b_vals, p_vals, 0.01)
        negligible = "✅ YES" if tost_p < 0.05 else "❌ NO"

        # Per-request overhead
        requests = REQUESTS_DEFAULT
        if 'requests_served' in h4_data.columns:
            req_vals = baseline['requests_served'].dropna().values
            if len(req_vals) > 0 and float(req_vals[0]) > 0:
                requests = float(req_vals[0])

        per_req = overhead / requests if requests > 0 else 0

        report_lines.append(
            f"| {metric} | {fmt(b_mean)} | {fmt(p_mean)} | "
            f"{fmt(abs(overhead))} | {overhead_pct:+.4f}% | {tost_p:.4e} | "
            f"[{fmt(ci_lo)}, {fmt(ci_hi)}] | {negligible} |"
        )

        results.append({"test": "H4", "metric": metric, "p_value": tost_p, "test_type": "TOST"})

    # Per-request summary
    b_cycles = baseline['cycles'].dropna().values.astype(float)
    p_cycles = profiled['cycles'].dropna().values.astype(float)
    if len(b_cycles) > 0 and len(p_cycles) > 0:
        per_req_cycles = (np.mean(p_cycles) - np.mean(b_cycles)) / REQUESTS_DEFAULT
        report_lines.append("")
        report_lines.append(
            f"> **Per-Request Claim:** For every request to the Go API, "
            f"`perf record -g -c 100K` adds ~{fmt(abs(per_req_cycles))} CPU cycles "
            f"of total overhead (direct + cache pollution)."
        )

    report_lines.append("")
    return results, report_lines


# ---------------------------------------------------------------------------
# Multiple Comparison Correction
# ---------------------------------------------------------------------------

def holm_bonferroni(all_results):
    """Apply Holm-Bonferroni correction across all p-values."""
    # Filter out NaN p-values
    valid = [r for r in all_results if not math.isnan(r['p_value'])]
    if not valid:
        return []

    # Sort by p-value
    valid.sort(key=lambda r: r['p_value'])
    m = len(valid)

    corrected = []
    for rank, r in enumerate(valid, 1):
        adj_alpha = 0.05 / (m - rank + 1)
        r['adjusted_alpha'] = adj_alpha
        r['significant_adjusted'] = r['p_value'] < adj_alpha
        r['rank'] = rank
        corrected.append(r)

    return corrected


def format_correction_table(corrected):
    """Format the Holm-Bonferroni correction table."""
    lines = []
    lines.append("## Multiple Comparison Correction (Holm-Bonferroni)")
    lines.append("")
    lines.append(f"Total tests: {len(corrected)}")
    lines.append("")
    lines.append("| Rank | Test | Metric | Test Type | Raw p | Adj α | Significant? |")
    lines.append("|------|------|--------|-----------|-------|-------|-------------|")

    for r in corrected:
        sig = "✅" if r['significant_adjusted'] else "❌"
        lines.append(
            f"| {r['rank']} | {r['test']} | {r['metric']} | "
            f"{r['test_type']} | {r['p_value']:.4e} | "
            f"{r['adjusted_alpha']:.4e} | {sig} |"
        )

    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

REQUESTS_DEFAULT = 1000

def main():
    parser = argparse.ArgumentParser(
        description="Statistical Analysis for All 4 Hypotheses"
    )
    parser.add_argument("dummy_csv", help="CSV from run_hypotheses_dummy.py")
    parser.add_argument("--goapi", type=str, default=None,
                        help="CSV from run_hypotheses_goapi.py")
    parser.add_argument("--output", type=str, default=None,
                        help="Output report path (markdown)")
    parser.add_argument("--requests", type=int, default=REQUESTS_DEFAULT,
                        help=f"Requests per iteration for per-request calc (default: {REQUESTS_DEFAULT})")
    args = parser.parse_args()

    global REQUESTS_DEFAULT
    REQUESTS_DEFAULT = args.requests

    # Load data
    print("  Loading data...")
    dummy_df = pd.read_csv(args.dummy_csv)
    print(f"  Dummy CSV: {len(dummy_df)} rows")

    goapi_df = None
    if args.goapi and os.path.exists(args.goapi):
        goapi_df = pd.read_csv(args.goapi)
        print(f"  Go API CSV: {len(goapi_df)} rows")

    all_results = []
    report = []

    report.append("# Hypothesis Testing Results")
    report.append("")
    report.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    report.append("")
    report.append("---")
    report.append("")

    # H1
    h1_results, h1_report = analyze_h1(dummy_df, goapi_df)
    all_results.extend(h1_results)
    report.extend(h1_report)

    # H2
    h2_results, h2_report = analyze_h2(dummy_df)
    all_results.extend(h2_results)
    report.extend(h2_report)

    # H3
    h3_results, h3_report = analyze_h3(dummy_df)
    all_results.extend(h3_results)
    report.extend(h3_report)

    # H4
    h4_results, h4_report = analyze_h4(goapi_df)
    all_results.extend(h4_results)
    report.extend(h4_report)

    # Multiple comparison correction
    report.append("---")
    report.append("")
    corrected = holm_bonferroni(all_results)
    report.extend(format_correction_table(corrected))

    # Summary
    report.append("## Summary Verdict")
    report.append("")
    for hyp in ["H1", "H2", "H3", "H4"]:
        hyp_tests = [r for r in corrected if r['test'] == hyp]
        if hyp_tests:
            n_sig = sum(1 for r in hyp_tests if r['significant_adjusted'])
            n_total = len(hyp_tests)
            report.append(f"- **{hyp}:** {n_sig}/{n_total} tests significant after correction")
        else:
            report.append(f"- **{hyp}:** No data")
    report.append("")

    # Output
    report_text = "\n".join(report)

    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, 'w') as f:
            f.write(report_text)
        print(f"\n  📊 Report saved to: {args.output}")
    else:
        # Auto-generate output path
        out_path = os.path.join(
            os.path.dirname(args.dummy_csv),
            "hypothesis_results_report.md"
        )
        with open(out_path, 'w') as f:
            f.write(report_text)
        print(f"\n  📊 Report saved to: {out_path}")

    # Also print to stdout
    print("\n" + report_text)


if __name__ == "__main__":
    main()
