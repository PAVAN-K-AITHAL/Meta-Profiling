# Meta-profiling results (two-sided measurement)

Sources: `pilot_h1.csv`, `pilot_h2.csv`

## Data quality

- Profiled windows paired with a baseline: **26** (workload_profiled rows: 26, tool_overhead rows: 26)
- Excluded for lost samples or throttling: **0**
- Conservation check |user lost − kernel − uncounted| / window: median 0.0000% (≈0 by construction; large values mean the window lengths differ)
- Baseline run-to-run variation of instructions:u (CV %):
  - H1 dummy_workload: 0.006%
  - H2 dummy_workload: 0.013%

## H1 / H4 — perf record -g -c 100K

### Cost to the workload and to the machine

| Group | N | Slowdown % | Time stolen % | IPC loss % | Uncounted share of lost | perf record % of core | Samples | Cycles/sample on CPU 6 | perf record cycles/sample |
|---|---|---|---|---|---|---|---|---|---|
| dummy_workload | 10 | 9.834 [9.822, 9.846] | 9.530 [9.517, 9.541] | 0.337 [0.331, 0.342] | 45.3% [45.2, 45.4] | 0.23 [0.22, 0.23] | 106.97K | 9.31K [9.29K, 9.34K] | 214.1 [211.5, 216.9] |

Values are mean [95% bootstrap CI]. *Slowdown* = work lost (instructions:u). *Time stolen* = user cycles lost. *IPC loss* = user code running slower (pollution). *Uncounted* = cycles inside the PMU interrupt handler, where every counter is off.

### Decomposition of CPU 6 cycles (mean per 5 s window)

| Workload | Window cycles | User cycles lost | Kernel cycles added | Uncounted | perf record cycles (CPU 7) | Old-method ratio |
|---|---|---|---|---|---|---|
| dummy_workload | 10.48B | 995.89M | 544.80M | 451.09M | 22.90M | 0.218% |

*Old-method ratio* is perf record's PID cycles / workload cycles: the number earlier drafts reported.

### H4 — TOST on slowdown (percentage points)

| Workload | Mean slowdown | 90% CI | TOST p (Δ=1.0) | TOST p (Δ=0.5) |
|---|---|---|---|---|
| dummy_workload | 9.834% | [9.822, 9.846] | 1.0000 ❌ | 1.0000 ❌ |

## H2 — Sampling period sweep (-g)

| -c | Runs (valid) | Samples/s | Slowdown % | perf record % of core | Cycles/sample on CPU 6 | perf record cycles/sample |
|---|---|---|---|---|---|---|
| 1.00M | 2 (2) | 2.22K | 2.620 [2.599, 2.641] | 0.03 [0.03, 0.03] | 11.48K [11.46K, 11.51K] | 272.5 [267.3, 277.8] |
| 500.00K | 2 (2) | 4.43K | 1.900 [1.893, 1.907] | 0.05 [0.05, 0.05] | 10.10K [10.07K, 10.12K] | 233.8 [231.5, 236.1] |
| 100.00K | 2 (2) | 21.30K | 9.861 [9.849, 9.874] | 0.23 [0.23, 0.23] | 9.36K [9.35K, 9.37K] | 214.5 [213.4, 215.5] |
| 50.00K | 2 (2) | 41.12K | 18.187 [18.162, 18.212] | 0.42 [0.41, 0.42] | 8.72K [8.72K, 8.72K] | 204.3 [203.2, 205.3] |
| 20.00K | 2 (2) | 93.54K | 38.227 [38.213, 38.241] | 0.74 [0.74, 0.74] | 7.95K [7.95K, 7.95K] | 162.6 [162.0, 163.2] |
| 10.00K | 2 (2) | 160.53K | 65.590 [65.552, 65.628] | 1.28 [1.27, 1.29] | 7.96K [7.95K, 7.97K] | 164.0 [162.4, 165.6] |
| 7.50K | 2 (2) | 195.86K | 79.579 [79.561, 79.597] | 1.66 [1.66, 1.66] | 7.94K [7.94K, 7.94K] | 174.0 [173.8, 174.2] |
| 5.00K | 2 (2) | 258.94K | 99.986 [99.986, 99.987] | 95.75 [93.85, 97.64] | 7.67K [7.67K, 7.68K] | 7.75K [7.59K, 7.90K] |

⚠️ **Cliff:** -c 5.00K (saturated). perf record can no longer keep up, so these periods are excluded from the fits below.

### Scaling fits (valid runs, cliff excluded)

| Cost | Exponent b [95% CI] | R² (log-log) | AIC linear | AIC power | Reading |
|---|---|---|---|---|---|
| Slowdown % (CPU 6) | 0.841 [0.743, 0.939] | 0.9670 | -12.1 | 41.6 | sub-linear |
| perf record cycles (CPU 7) | 0.893 [0.873, 0.914] | 0.9987 | 430.3 | 436.4 | sub-linear |

b = 1 means cost is proportional to the number of samples; lower AIC is the better model.

## Figures

- `/home/user/Desktop/Meta-Profiling/meta_profiling_deliverables/results/paper_20261128_205641/pilot/figures/fig_h2_scaling.pdf`

## Holm-Bonferroni correction (all tests above)

| Rank | Test | Detail | Raw p | Holm α | Significant? |
|---|---|---|---|---|---|
| 1 | H2 exponent = 1 | perf record cycles (CPU 7) | 8.88e-08 | 1.25e-02 | ✅ |
| 2 | H2 exponent = 1 | Slowdown % (CPU 6) | 0.0040 | 1.67e-02 | ✅ |
| 3 | H4 TOST | dummy_workload slowdown Δ=1.0pp | 1.0000 | 2.50e-02 | ❌ |
| 4 | H4 TOST | dummy_workload slowdown Δ=0.5pp | 1.0000 | 5.00e-02 | ❌ |
