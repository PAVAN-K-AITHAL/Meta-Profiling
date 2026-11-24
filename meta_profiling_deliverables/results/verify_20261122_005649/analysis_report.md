# Hypothesis Testing Results

Generated: 2026-11-22 01:09

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
| cycles | 50 | 21.89M | 5.36M | 18.49M | 40.02M |
| cache_misses | 50 | 491.77K | 110.70K | 389.68K | 895.29K |
| branch_misses | 50 | 65.13K | 14.15K | 56.14K | 114.45K |
| context_switches | 50 | 42.9 | 8.2 | 38.0 | 72.0 |

### Overhead Ratio (Tool / Workload)

| Metric | Workload Mean | Tool Mean | Ratio |
|--------|-------------|-----------|-------|
| cycles | 10.48B | 21.89M | 0.2088% |
| cache_misses | 187.63M | 491.77K | 0.2621% |
| branch_misses | 137.12K | 65.13K | 47.4946% |
| context_switches | 45.4 | 42.9 | 94.4469% |

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
| cycles | 10.48B | 21.89M | 0.2088% | 1.6224e-60 | 1.6334e-39 | 7.1833e-78 | ✅ YES |
| cache_misses | 187.63M | 491.77K | 0.2621% | 9.9958e-01 | 9.9964e-01 | 9.9943e-01 | ❌ NO |
| branch_misses | 137.12K | 65.13K | 47.4946% | 1.0000e+00 | 1.0000e+00 | 1.0000e+00 | ❌ NO |
| context_switches | 45.4 | 42.9 | 94.4469% | 1.0000e+00 | 1.0000e+00 | 1.0000e+00 | ❌ NO |

> **Summary (dummy workload):** `perf record -g -c 100K` consumed 21.89M cycles per measurement window, which is 0.2088% of the workload's own 10.48B cycles.

---

## Multiple Comparison Correction (Holm-Bonferroni)

Total tests: 4

| Rank | Test | Metric | Test Type | Raw p | Adj α | Significant? |
|------|------|--------|-----------|-------|-------|-------------|
| 1 | H4 | cycles | TOST_1pct_dummy | 1.6224e-60 | 1.2500e-02 | ✅ |
| 2 | H4 | cache_misses | TOST_1pct_dummy | 9.9958e-01 | 1.6667e-02 | ❌ |
| 3 | H4 | branch_misses | TOST_1pct_dummy | 1.0000e+00 | 2.5000e-02 | ❌ |
| 4 | H4 | context_switches | TOST_1pct_dummy | 1.0000e+00 | 5.0000e-02 | ❌ |

## Summary Verdict

- **H1:** No data
- **H2:** No data
- **H3:** No data
- **H4:** 1/4 tests significant after correction
