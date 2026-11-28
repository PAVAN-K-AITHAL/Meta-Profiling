# Hypothesis Testing Results

Generated: 2026-07-20 19:46

---

## H1: Constant Absolute Cost

> **H₀:** The absolute hardware cost of `perf record -g -c 100K` differs
> significantly between workloads.
> **H₁:** The cost is approximately constant (fixed hardware tax).

### perf record's Own Resource Consumption

| Metric | N | Mean | Std | Min | Max |
|--------|---|------|-----|-----|-----|
| cycles | 1000 | 2.17M | 1.74M | 200.00K | 12.00M |
| cache_misses | 1000 | 4.35K | 3.22K | 1.60K | 22.00K |
| branch_misses | 1000 | 3.46K | 3.60K | 300.0 | 24.40K |
| context_switches | 1000 | 8.9 | 3.8 | 6.0 | 25.0 |

### Overhead Ratio (Tool / Workload)

| Metric | Workload Mean | Tool Mean | Ratio |
|--------|-------------|-----------|-------|
| cycles | 10.01B | 2.17M | 0.0217% |
| cache_misses | 9.88M | 4.35K | 0.0440% |
| branch_misses | 898.40K | 3.46K | 0.3848% |
| context_switches | 53.4 | 8.9 | 16.5892% |

> ℹ️ Only one workload group available. Add more workloads
> (e.g., stress-ng, sysbench) for cross-workload comparison.

## H2: Linear Scaling with Sampling Frequency

> **H₀:** The overhead scales linearly with sampling frequency (1/c).
> **H₁:** The relationship is non-linear (super-linear or sub-linear).

### cycles

**Linear Fit:** y = 2.12e+09·x + 1.78e+06
  R² = 0.1581, slope p = 1.6388e-226

**Non-linearity F-test:** F = 2387.26, p = 1.1102e-16
  Non-linear? YES (non-linear)

| -c Value | N | Mean | Std |
|----------|---|------|-----|
| 1.00K | 1000 | 3.71M | 1.80M |
| 10.00K | 1000 | 3.82M | 1.86M |
| 50.00K | 1000 | 2.56M | 1.24M |
| 100.00K | 1000 | 2.17M | 1.60M |
| 500.00K | 1000 | 490.85K | 659.12K |
| 1.00M | 1000 | 347.55K | 352.39K |

### cache_misses

**Linear Fit:** y = 5.43e+06·x + 3.68e+03
  R² = 0.2137, slope p = 0.0000e+00

**Non-linearity F-test:** F = 3138.66, p = 1.1102e-16
  Non-linear? YES (non-linear)

| -c Value | N | Mean | Std |
|----------|---|------|-----|
| 1.00K | 1000 | 8.65K | 3.93K |
| 10.00K | 1000 | 8.63K | 3.75K |
| 50.00K | 1000 | 4.96K | 2.37K |
| 100.00K | 1000 | 4.37K | 3.21K |
| 500.00K | 1000 | 935.2 | 1.54K |
| 1.00M | 1000 | 691.6 | 934.1 |

### branch_misses

**Linear Fit:** y = 2.50e+06·x + 2.93e+03
  R² = 0.1057, slope p = 1.0326e-147

**Non-linearity F-test:** F = 1460.09, p = 1.1102e-16
  Non-linear? YES (non-linear)

| -c Value | N | Mean | Std |
|----------|---|------|-----|
| 1.00K | 1000 | 5.19K | 2.53K |
| 10.00K | 1000 | 5.53K | 2.79K |
| 50.00K | 1000 | 3.38K | 2.22K |
| 100.00K | 1000 | 3.54K | 3.26K |
| 500.00K | 1000 | 1.50K | 1.10K |
| 1.00M | 1000 | 1.30K | 641.6 |

## H3: Call-Graph (`-g`) Ablation

> **H₀:** Removing `-g` does NOT reduce overhead.
> **H₁:** Removing `-g` significantly reduces overhead.

| Metric | With -g (mean) | Without -g (mean) | Diff | Diff % | p-value | Cohen's d | Significant? |
|--------|---------------|------------------|------|--------|---------|-----------|-------------|
| cycles | 2.08M | 1.05M | 1.04M | +99.23% | 6.2955e-114 | 0.86 | ✅ YES |
| cache_misses | 4.50K | 2.11K | 2.39K | +113.49% | 8.1432e-186 | 0.99 | ✅ YES |
| branch_misses | 3.37K | 1.96K | 1.40K | +71.59% | 8.0369e-17 | 0.56 | ✅ YES |
| context_switches | 8.9 | 6.2 | 2.8 | +44.66% | 1.7050e-278 | 0.99 | ✅ YES |

> **Attribution:** Call-graph unwinding (`-g`) accounts for ~49.8% of perf record's total cycle overhead.

## H4: Practical Negligibility (< 1% Overhead)

> **H₀:** Profiler overhead ≥ 1% of workload resource consumption.
> **H₁:** Profiler overhead < 1% (negligible).

### Overhead Ratio: perf record / dummy workload

| Metric | Workload Mean | Tool Mean | Ratio % | TOST p (Δ=1%) | TOST p (Δ=0.5%) | TOST p (Δ=2%) | < 1%? |
|--------|-------------|-----------|---------|---------------|-----------------|---------------|-------|
| cycles | 10.01B | 2.17M | 0.0216% | 0.0000e+00 | 0.0000e+00 | 0.0000e+00 | ✅ YES |
| cache_misses | 9.88M | 4.34K | 0.0439% | 1.0000e+00 | 1.0000e+00 | 1.0000e+00 | ❌ NO |
| branch_misses | 898.40K | 3.45K | 0.3842% | 5.8994e-251 | 1.5386e-15 | 0.0000e+00 | ✅ YES |
| context_switches | 53.4 | 8.9 | 16.5814% | 1.0000e+00 | 1.0000e+00 | 1.0000e+00 | ❌ NO |

> **Summary (dummy workload):** `perf record -g -c 100K` consumed 2.17M cycles per measurement window, which is 0.0217% of the workload's own 10.01B cycles.

---

## Multiple Comparison Correction (Holm-Bonferroni)

Total tests: 14

| Rank | Test | Metric | Test Type | Raw p | Adj α | Significant? |
|------|------|--------|-----------|-------|-------|-------------|
| 1 | H2 | cache_misses | OLS_slope | 0.0000e+00 | 3.5714e-03 | ✅ |
| 2 | H4 | cycles | TOST_1pct_dummy | 0.0000e+00 | 3.8462e-03 | ✅ |
| 3 | H3 | context_switches | Mann-Whitney U | 1.7050e-278 | 4.1667e-03 | ✅ |
| 4 | H4 | branch_misses | TOST_1pct_dummy | 5.8994e-251 | 4.5455e-03 | ✅ |
| 5 | H2 | cycles | OLS_slope | 1.6388e-226 | 5.0000e-03 | ✅ |
| 6 | H3 | cache_misses | Mann-Whitney U | 8.1432e-186 | 5.5556e-03 | ✅ |
| 7 | H2 | branch_misses | OLS_slope | 1.0326e-147 | 6.2500e-03 | ✅ |
| 8 | H3 | cycles | Mann-Whitney U | 6.2955e-114 | 7.1429e-03 | ✅ |
| 9 | H3 | branch_misses | Mann-Whitney U | 8.0369e-17 | 8.3333e-03 | ✅ |
| 10 | H2 | cycles | nonlinearity_F | 1.1102e-16 | 1.0000e-02 | ✅ |
| 11 | H2 | cache_misses | nonlinearity_F | 1.1102e-16 | 1.2500e-02 | ✅ |
| 12 | H2 | branch_misses | nonlinearity_F | 1.1102e-16 | 1.6667e-02 | ✅ |
| 13 | H4 | cache_misses | TOST_1pct_dummy | 1.0000e+00 | 2.5000e-02 | ❌ |
| 14 | H4 | context_switches | TOST_1pct_dummy | 1.0000e+00 | 5.0000e-02 | ❌ |

## Summary Verdict

- **H1:** No data
- **H2:** 6/6 tests significant after correction
- **H3:** 4/4 tests significant after correction
- **H4:** 2/4 tests significant after correction
