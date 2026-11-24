# Hypothesis Testing Results

Generated: 2026-11-24 17:05

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
| cycles | 50 | 22.98M | 261.50K | 22.42M | 23.55M |
| cache_misses | 50 | 401.20K | 12.28K | 364.11K | 421.92K |
| branch_misses | 50 | 53.24K | 814.7 | 51.56K | 55.84K |
| context_switches | 50 | 36.2 | 0.4 | 36.0 | 37.0 |

### Overhead Ratio (Tool / Workload)

| Metric | Workload Mean | Tool Mean | Ratio |
|--------|-------------|-----------|-------|
| cycles | 10.48B | 22.98M | 0.2192% |
| cache_misses | 48.06M | 401.20K | 0.8348% |
| branch_misses | 76.94M | 53.24K | 0.0692% |
| context_switches | 5.5 | 36.2 | 661.3139% |

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
| cycles | 10.48B | 22.98M | 0.2192% | 1.8611e-124 | 1.0772e-102 | 5.2979e-142 | ✅ YES |
| cache_misses | 48.06M | 401.20K | 0.8348% | 3.4214e-42 | 1.0000e+00 | 1.6005e-83 | ✅ YES |
| branch_misses | 76.94M | 53.24K | 0.0692% | 9.4641e-146 | 2.3473e-129 | 2.8112e-161 | ✅ YES |
| context_switches | 5.5 | 36.2 | 661.3139% | 1.0000e+00 | 1.0000e+00 | 1.0000e+00 | ❌ NO |

> **Summary (dummy workload):** `perf record -g -c 100K` consumed 22.98M cycles per measurement window, which is 0.2192% of the workload's own 10.48B cycles.

---

## Multiple Comparison Correction (Holm-Bonferroni)

Total tests: 4

| Rank | Test | Metric | Test Type | Raw p | Adj α | Significant? |
|------|------|--------|-----------|-------|-------|-------------|
| 1 | H4 | branch_misses | TOST_1pct_dummy | 9.4641e-146 | 1.2500e-02 | ✅ |
| 2 | H4 | cycles | TOST_1pct_dummy | 1.8611e-124 | 1.6667e-02 | ✅ |
| 3 | H4 | cache_misses | TOST_1pct_dummy | 3.4214e-42 | 2.5000e-02 | ✅ |
| 4 | H4 | context_switches | TOST_1pct_dummy | 1.0000e+00 | 5.0000e-02 | ❌ |

## Summary Verdict

- **H1:** No data
- **H2:** No data
- **H3:** No data
- **H4:** 3/4 tests significant after correction
