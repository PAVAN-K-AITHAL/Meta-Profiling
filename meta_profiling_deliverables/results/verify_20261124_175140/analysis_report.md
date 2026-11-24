# Hypothesis Testing Results

Generated: 2026-11-24 18:04

## Data Quality (perf record throttling / lost samples)

**Dummy workload:** 0 of 50 profiled runs excluded (throttled or lost samples); 0 with unreadable flags kept.

---

> **Metric note:** `cache_misses` contains L1-dcache-load-misses
> as measured by `perf stat`. This is more specific than the former
> bpftrace combined cache-misses count.

## H1: Constant Absolute Cost

> **H₀:** The absolute hardware cost of `perf record -g -c 100K` differs
> significantly between workloads.
> **H₁:** The cost is approximately constant (fixed hardware tax).

### perf record's Own Resource Consumption

| Metric | N | Mean | Std | Min | Max |
|--------|---|------|-----|-----|-----|
| cycles | 50 | 23.20M | 272.99K | 22.54M | 23.71M |
| cache_misses | 50 | 403.87K | 13.23K | 375.26K | 431.53K |
| branch_misses | 50 | 53.94K | 781.2 | 52.04K | 55.81K |
| context_switches | 50 | 36.2 | 0.5 | 36.0 | 38.0 |

### Overhead Ratio (Tool / Workload)

| Metric | Workload Mean | Tool Mean | Ratio |
|--------|-------------|-----------|-------|
| cycles | 10.48B | 23.20M | 0.2213% |
| cache_misses | 48.07M | 403.87K | 0.8401% |
| branch_misses | 76.95M | 53.94K | 0.0701% |
| context_switches | 5.5 | 36.2 | 656.5217% |

> ℹ️ Only one workload group available. Add more workloads
> (e.g., stress-ng, sysbench) for cross-workload comparison.

## H2: Linear Scaling with Sampling Frequency

> **H₀:** The overhead scales linearly with sampling frequency (1/c).
> **H₁:** The relationship is non-linear (super-linear or sub-linear).

⚠️ No H2 data found.

## H3: Call-Graph (`-g`) Ablation

> **H₀:** Removing `-g` does NOT reduce overhead.
> **H₁:** Removing `-g` significantly reduces overhead.

⚠️ No H3 data found.

## H4: Practical Negligibility (< 1% Overhead)

> **H₀:** Profiler overhead ≥ 1% of workload resource consumption.
> **H₁:** Profiler overhead < 1% (negligible).

### Overhead Ratio: perf record / dummy workload

| Metric | Workload Mean | Tool Mean | Ratio % | TOST p (Δ=1%) | TOST p (Δ=0.5%) | TOST p (Δ=2%) | < 1%? |
|--------|-------------|-----------|---------|---------------|-----------------|---------------|-------|
| cycles | 10.48B | 23.20M | 0.2213% | 1.6828e-123 | 1.2291e-101 | 4.4522e-141 | ✅ YES |
| cache_misses | 48.07M | 403.87K | 0.8401% | 5.8529e-40 | 1.0000e+00 | 7.7464e-82 | ✅ YES |
| branch_misses | 76.95M | 53.94K | 0.0701% | 6.6669e-147 | 1.7463e-130 | 1.9327e-162 | ✅ YES |
| context_switches | 5.5 | 36.2 | 656.5217% | 1.0000e+00 | 1.0000e+00 | 1.0000e+00 | ❌ NO |

> **Summary (dummy workload):** `perf record -g -c 100K` consumed 23.20M cycles per measurement window, which is 0.2213% of the workload's own 10.48B cycles.

---

## Multiple Comparison Correction (Holm-Bonferroni)

Total tests: 4

| Rank | Test | Metric | Test Type | Raw p | Adj α | Significant? |
|------|------|--------|-----------|-------|-------|-------------|
| 1 | H4 | branch_misses | TOST_1pct_dummy | 6.6669e-147 | 1.2500e-02 | ✅ |
| 2 | H4 | cycles | TOST_1pct_dummy | 1.6828e-123 | 1.6667e-02 | ✅ |
| 3 | H4 | cache_misses | TOST_1pct_dummy | 5.8529e-40 | 2.5000e-02 | ✅ |
| 4 | H4 | context_switches | TOST_1pct_dummy | 1.0000e+00 | 5.0000e-02 | ❌ |

## Summary Verdict

- **H1:** No data
- **H2:** No data
- **H3:** No data
- **H4:** 3/4 tests significant after correction
