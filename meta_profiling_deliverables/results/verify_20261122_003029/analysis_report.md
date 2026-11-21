# Hypothesis Testing Results

Generated: 2026-11-22 00:32

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
| cycles | 5 | 2.99M | 4.06M | 101.30K | 8.73M |
| cache_misses | 5 | 63.13K | 91.35K | 1.10K | 203.81K |
| branch_misses | 5 | 9.60K | 12.30K | 1.13K | 28.17K |
| context_switches | 5 | 9.6 | 6.8 | 5.0 | 20.0 |

### Overhead Ratio (Tool / Workload)

| Metric | Workload Mean | Tool Mean | Ratio |
|--------|-------------|-----------|-------|
| cycles | 14.96B | 2.99M | 0.0200% |
| cache_misses | 426.94M | 63.13K | 0.0148% |
| branch_misses | 124.68K | 9.60K | 7.6961% |
| context_switches | 47.0 | 9.6 | 20.4255% |

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
| cycles | 14.96B | 2.99M | 0.0200% | 7.0886e-08 | 1.2276e-06 | 4.2575e-09 | ✅ YES |
| cache_misses | 426.94M | 63.13K | 0.0148% | 2.7996e-04 | 5.9975e-03 | 1.4378e-05 | ✅ YES |
| branch_misses | 124.68K | 9.60K | 7.6961% | 9.0005e-01 | 9.1335e-01 | 8.6689e-01 | ❌ NO |
| context_switches | 47.0 | 9.6 | 20.4255% | 9.7765e-01 | 9.7921e-01 | 9.7409e-01 | ❌ NO |

> **Summary (dummy workload):** `perf record -g -c 100K` consumed 2.99M cycles per measurement window, which is 0.0200% of the workload's own 14.96B cycles.

---

## Multiple Comparison Correction (Holm-Bonferroni)

Total tests: 4

| Rank | Test | Metric | Test Type | Raw p | Adj α | Significant? |
|------|------|--------|-----------|-------|-------|-------------|
| 1 | H4 | cycles | TOST_1pct_dummy | 7.0886e-08 | 1.2500e-02 | ✅ |
| 2 | H4 | cache_misses | TOST_1pct_dummy | 2.7996e-04 | 1.6667e-02 | ✅ |
| 3 | H4 | branch_misses | TOST_1pct_dummy | 9.0005e-01 | 2.5000e-02 | ❌ |
| 4 | H4 | context_switches | TOST_1pct_dummy | 9.7765e-01 | 5.0000e-02 | ❌ |

## Summary Verdict

- **H1:** No data
- **H2:** No data
- **H3:** No data
- **H4:** 2/4 tests significant after correction
