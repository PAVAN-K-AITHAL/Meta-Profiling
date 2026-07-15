# Pilot Run Results: perf record Overhead on Dummy Workload

**Date:** 2026-07-15
**Workload:** Dummy
**Profiler:** `perf record -g -c 100 K` (and variants)

---

## H1: Profiler Absolute Cost & Overhead

**Description:** The following tables show the absolute hardware events consumed by `perf record -g -c 100 K` and compare them against the dummy workload itself to calculate the relative overhead.

### perf record's Own Resource Consumption

| Metric | N | Mean | Std | Min | Max |
|--------|---|------|-----|-----|-----|
| cycles | 50 | 1.83 M | 1.88 M | 100 K | 10.5 M |
| cache misses | 50 | 4.34 K | 3.64 K | 900 | 20 K |
| branch misses | 50 | 3.37 K | 4.08 K | 200 | 22.8 K |
| context switches | 50 | 8.7 | 4.1 | 5.0 | 24.0 |

### Overhead Ratio (Tool / Workload)

| Metric | Workload Mean | Tool Mean | Ratio |
|--------|-------------|-----------|-------|
| cycles | 11.73 B | 1.83 M | 0.0156% |
| cache misses | 8.78 M | 4.34 K | 0.0494% |
| branch misses | 976.5 K | 3.37 K | 0.3455% |
| context switches | 44.9 | 8.7 | 19.33% |

> **Significant Observation:** The absolute cycle cost of the tool is approximately 1.83 M cycles per window. When compared to the 11.73 B cycles of the workload, the overhead is extremely low (0.0156%). Context switches see a higher relative impact, likely because the dummy workload does very few context switches itself.

---

## H2: Scaling with Sampling Frequency

**Description:** This table evaluates whether the profiler's resource consumption scales linearly with the sampling frequency (the `-c` flag). The F-test for non-linearity yielded highly significant results (p < 0.001) across all metrics.

### Cycles Scaling

| `-c` Value (Frequency) | N | Mean | Std |
|----------|---|------|-----|
| 1 K | 50 | 3.21 M | 1.47 M |
| 10 K | 50 | 3.40 M | 1.56 M |
| 50 K | 50 | 2.46 M | 1.39 M |
| 100 K | 50 | 2.16 M | 1.62 M |
| 500 K | 50 | 509 K | 659.7 K |
| 1 M | 50 | 319 K | 313.1 K |

> **Significant Observation:** The relationship between sampling frequency and overhead is significantly non-linear. Counterintuitively, the highest overhead is observed at higher sampling frequencies (lower `-c` values like 1 K and 10 K), while it drops off at `-c 500 K` and `-c 1 M`. 

---

## H3: Call-Graph (`-g`) Ablation

**Description:** This table compares the resource consumption of `perf record` with and without the call-graph (`-g`) flag to quantify its specific contribution to the profiling overhead.

| Metric | With `-g` (mean) | Without `-g` (mean) | Diff | Diff % | Significant? |
|--------|---------------|------------------|------|--------|-------------|
| cycles | 2.31 M | 1.15 M | 1.17 M | +101.6% | ✅ YES |
| cache misses | 4.78 K | 2.43 K | 2.35 K | +96.8% | ✅ YES |
| branch misses | 3.76 K | 2.20 K | 1.57 K | +71.3% | ✅ YES |
| context switches | 8.7 | 6.2 | 2.6 | +41.5% | ✅ YES |

> **Significant Observation:** Call-graph unwinding (`-g`) accounts for roughly 50.4% of `perf record`'s total cycle overhead (adding 1.17 M cycles on top of the base 1.15 M cycles). Removing the flag significantly halves the cost across most metrics.

---

## H4: Practical Negligibility (< 1% Overhead)

**Description:** This table uses TOST (Two One-Sided Tests) to statistically verify if the overhead is practically negligible, which we defined as being strictly less than 1% of the workload's resource consumption.

| Metric | Workload Mean | Tool Mean | Ratio % | TOST p-value (Δ=1%) | < 1%? |
|--------|-------------|-----------|---------|---------------|-------|
| cycles | 11.73 B | 1.83 M | 0.0156% | < 0.0001 | ✅ YES |
| cache misses | 8.78 M | 4.34 K | 0.0494% | 0.9999 | ❌ NO |
| branch misses | 976.5 K | 3.37 K | 0.3455% | < 0.0001 | ✅ YES |
| context switches | 44.9 | 8.7 | 19.33% | 1.0000 | ❌ NO |

> **Significant Observation:** The overhead for cycles and branch misses is statistically confirmed to be less than 1% (practically negligible).
