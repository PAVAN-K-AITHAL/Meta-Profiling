# Hypothesis Framework: Meta-Profiling Study

## Project: "Who Watches the Watchmen? — Quantifying and Diagnosing the Hidden Cost of Observability"

> This document defines the formal hypotheses, statistical tests, experiment designs, and expected outcomes for the meta-profiling research study. It replaces the original single null hypothesis ("the profiler's hardware footprint is no different from the baseline") with a structured, multi-hypothesis framework.

---

## Why We Changed the Hypothesis

### The Original (Flawed) Hypothesis

> **Old H₀:** "The profiler's hardware footprint is no different from the baseline workload. Any small differences in overhead are due to random variation."

### Why It Was Wrong

This hypothesis is **trivially rejectable**. We already know `perf record` does work — it fires NMI interrupts, writes to ring buffers, walks the call stack (with `-g`), and resolves kernel symbols. It *must* consume some cycles. A test asking "is the overhead zero?" will always answer "yes, it's non-zero" (p ≈ 0), making the test scientifically useless.

Running 1,000 iterations to prove something that was never in question adds no value. The interesting questions are:
- **How much** overhead is there? (Magnitude)
- **Is it constant** across workloads? (Predictability)
- **Does it scale linearly** with sampling frequency? (Model)
- **Is it small enough** to be negligible in production? (Practical significance)

### The New Framework

We restructure the study around **four testable hypotheses**, each answering a distinct research question:

| Hypothesis | Research Question |
|-----------|------------------|
| H1 | Is the profiler's absolute cost constant across different workloads? |
| H2 | Does the overhead scale linearly with sampling frequency? |
| H3 | How much does call-graph recording (`-g`) contribute to the total overhead? |
| H4 | Is the total overhead small enough to be considered negligible (< 1%) in production? |

---

## Test Environment & System Configuration

### Hardware
- **CPU:** Intel Xeon E5-2620 v4 @ 2.10 GHz (Broadwell-EP)
- **Sockets:** 2 (8 physical cores × 2 HyperThreads = 16 logical CPUs per socket, 32 total)
- **L3 Cache:** 20 MB per socket (shared among all cores on that socket)
- **RAM:** 32 GB
- **NUMA:** 2 nodes (one per socket)

### CPU Isolation
- **Kernel boot parameters:** `isolcpus=6,7,14,15` (detached from SMP scheduler)
- **Process pinning:** via `taskset`

### CPU Pinning Layout

| Layer | Process | CPU | Physical Core | Socket | Rationale |
|-------|---------|-----|---------------|--------|-----------|
| Target | Workload (`lat_mem_rd` / Go API) | 6 | C6 | S0 | Isolated from OS scheduler |
| Inner Tool | `perf record` (being studied) | 7 | C7 | S0 | Same socket as target (realistic deployment; shares L3 but not L1/L2) |
| Outer Ruler (counting) | `perf stat` | 14 | C6 | S1 | Different socket → no L3 cache pollution with inner tool |
| Outer Ruler (diagnostic) | eBPF kprobes (bpftrace) | 15 | C7 | S1 | Different socket → clean diagnostic measurement |

> **Note:** CPUs 6 and 7 are on different physical cores (verified via `/sys/devices/system/cpu/cpu6/topology/thread_siblings_list`) so they do NOT share L1/L2. They share L3 only, which is the realistic scenario when a profiler runs alongside an application on the same machine.

### System Hardening Checklist

Before ANY experiment runs, the following MUST be configured:

| Setting | Command | Why |
|---------|---------|-----|
| Disable Turbo Boost | `echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo` | Prevents unpredictable frequency scaling; makes "cycles" a stable unit |
| Disable ASLR | `echo 0 > /proc/sys/kernel/randomize_va_space` | Eliminates cache layout variability between runs |
| Disable Transparent Huge Pages | `echo never > /sys/kernel/mm/transparent_hugepage/enabled` | Prevents unpredictable page fault latency |
| Move IRQs off isolated cores | `for irq in /proc/irq/*/smp_affinity_list; do echo 0-5 > $irq; done` | Prevents hardware interrupts from landing on measurement cores |
| Stop NTP/Chrony | `systemctl stop ntp chronyd` | Prevents background CPU activity from time sync |
| Stop non-essential services | `systemctl isolate multi-user.target` | Eliminates background daemon noise |
| Record system config | `lscpu; dmesg; cat /proc/cmdline; uname -a` | Proves system was correctly configured at experiment time |

### Workloads

| Workload | Type | Description | Measurement Unit |
|----------|------|-------------|-----------------|
| `lat_mem_rd` (lmbench) | CPU/Memory-bound | Pointer-chasing memory latency benchmark; defeats hardware prefetchers. Run with `-t` flag. | Fixed duration (2s window) |
| Go API + PostgreSQL + Nginx | Production-representative | Containerized Go HTTP API with database queries under load | Fixed work (N requests) |

### Dual Outer Measurement Tools

| Tool | Role | What It Measures | Accuracy |
|------|------|-----------------|----------|
| `perf stat` (≤ 4 GP events, no multiplexing) | **Exact counting** | Total overhead in cycles, cache-misses, branch-misses, page-faults | Hardware-exact (no sampling, no extrapolation) |
| eBPF kprobes (bpftrace) | **Diagnostic** | Per-function latency (which kernel functions cause the overhead) | ~200ns kprobe trampoline overhead per measurement |

> **Why both?** `perf stat` answers "how much total overhead?" with exact counts. eBPF answers "which kernel functions contribute to that overhead?" with per-function nanosecond timing. They are complementary.

> **Why not eBPF alone for counting?** The `hardware:cycles:100000` overflow probe in bpftrace fires every 100K cycles, introducing discretization error (e.g., 523,456 actual cycles → reported as 500,000). `perf stat` counts every single event with zero loss.

---

## Hypothesis 1: Constant Absolute Cost Across Workloads

### Research Question

> Is the absolute hardware overhead of `perf record` (in cycles, cache-misses, etc.) a **fixed cost** that remains constant regardless of what the target workload is doing?

### Formal Hypotheses

- **H₀:** The absolute hardware overhead of `perf record -g -c 100000` differs significantly between workloads. The profiler's cost depends on the type of application being profiled.

- **H₁:** The absolute overhead is approximately constant across workloads. The profiler imposes a fixed hardware tax regardless of what the target application does.

### Why This Matters

If H₁ is true, it explains a key observation from our earlier experiments: the **relative** overhead (%) appears massive on idle/light workloads but tiny on busy ones. The explanation is simple — the numerator (profiler cost) is constant; only the denominator (workload cost) changes. This gives engineers a predictive model: "If I run `perf record`, it will cost me ~X cycles per second, no matter what my application does."

### Experiment Design

#### Variables
- **Independent variable:** Workload type (categorical — `lat_mem_rd`, Go API, I/O-bound)
- **Dependent variable:** Absolute hardware counter values for `perf record` (cycles, cache-misses, branch-misses, page-faults)
- **Controlled variables:** `perf record` configuration (`-g -c 100000`), CPU pinning, system hardening, measurement duration/work

#### Procedure

For each workload:
1. Pin the target workload to CPU 6.
2. Start `perf record -g -c 100000` on the target, pinned to CPU 7.
3. Start `perf stat` on `perf record`'s PID, pinned to CPU 14.
4. Run the workload for the measurement window.
5. Stop `perf stat` → record hardware counter values for `perf record`.
6. Stop `perf record`.
7. Cool-down period (2 seconds).
8. Repeat N times.

#### Measurement Details

| Workload | Measurement Approach | Window |
|----------|---------------------|--------|
| `lat_mem_rd` | Constant time (2-second window) | 2s |
| Go API | Constant work (1,000 requests via `hey -n 1000 -c 10`) | Variable time |
| I/O-bound (optional) | Constant work (e.g., write 100 MB via `fio`) | Variable time |

> **Note on Go API:** We use constant-work measurement (fixed request count) instead of constant-time to eliminate variance from request arrival burstiness. See the "Constant Work Methodology" section below.

#### Iterations
- Pilot run: 50 iterations per workload to estimate effect sizes.
- Full run: Sample size determined by power analysis (see "Sample Size Calculation" section).
- Expected: 200–500 iterations per workload based on pilot variance.

### Statistical Test

#### Primary Test: One-Way Welch's ANOVA

Welch's ANOVA (not standard ANOVA) because we cannot assume equal variances across workloads — a CPU-bound workload will have different variance characteristics than an I/O-bound one.

- **Factor:** Workload type (3 levels)
- **Response:** Absolute `perf record` cycle count (or cache-miss count, etc.)
- **Significance level:** α = 0.05 (adjusted for multiple comparisons — see "Multiple Comparisons" section)

#### Assumption Checks (Before Running ANOVA)
1. **Normality:** Shapiro-Wilk test on each workload group. If p < 0.05, use Kruskal-Wallis (non-parametric alternative).
2. **Equal variances:** Levene's test. If p < 0.05, Welch's ANOVA handles this automatically (which is why we use Welch's).

#### Post-Hoc Test
If ANOVA is significant (p < α): Pairwise Games-Howell tests (does not assume equal variances) to identify which workload pairs differ.

#### Equivalence Confirmation (Critical!)
If ANOVA is NOT significant (p > α), this does **not** prove the costs are equal — it only means we failed to find a difference. To **prove** equivalence, we additionally run:

- **TOST (Two One-Sided Tests)** on each pair of workloads.
- Equivalence margin: Δ = 10% of the mean overhead (e.g., if mean overhead is 500K cycles, Δ = 50K cycles).
- If both one-sided tests reject → we can claim the costs are equivalent within ±10%.

#### What to Report
- Mean ± SD of `perf record`'s absolute overhead per workload.
- F-statistic, p-value, and η² (eta-squared) effect size from ANOVA.
- TOST results with 90% confidence intervals.
- A table and bar chart comparing absolute overhead across workloads.

#### Expected Outcome
We expect H₁ to hold: the absolute cycle overhead of `perf record` should be approximately constant (~500K cycles per 2-second window) across workloads, because the profiler's NMI handler, ring buffer writes, and symbol resolution are independent of what the target application does.

### Implementation (Python)

```python
from scipy import stats
import numpy as np

# After collecting data:
# lat_mem_rd_cycles = [...] # N values: perf record's cycle count
# goapi_cycles = [...]       # N values
# io_cycles = [...]          # N values

# 1. Normality check
for name, data in [("lat_mem_rd", lat_mem_rd_cycles), 
                    ("goapi", goapi_cycles), ("io", io_cycles)]:
    stat, p = stats.shapiro(data[:5000])  # Shapiro-Wilk
    print(f"{name}: Shapiro-Wilk p={p:.4f} {'(normal)' if p > 0.05 else '(non-normal)'}")

# 2. Levene's test for equal variances
stat, p = stats.levene(lat_mem_rd_cycles, goapi_cycles, io_cycles)
print(f"Levene's test: p={p:.4f}")

# 3. Welch's ANOVA / Kruskal-Wallis
stat, p = stats.kruskal(lat_mem_rd_cycles, goapi_cycles, io_cycles)
print(f"Kruskal-Wallis H={stat:.2f}, p={p:.6f}")

# 4. Effect size (eta-squared)
N = len(lat_mem_rd_cycles) + len(goapi_cycles) + len(io_cycles)
eta_sq = stat / (N - 1)
print(f"Effect size eta-squared = {eta_sq:.4f}")

# 5. Post-hoc pairwise (if significant)
import scikit_posthocs as sp
data = np.concatenate([lat_mem_rd_cycles, goapi_cycles, io_cycles])
groups = (["lat_mem_rd"] * len(lat_mem_rd_cycles) + 
          ["goapi"] * len(goapi_cycles) + 
          ["io"] * len(io_cycles))
result = sp.posthoc_ttest(data, groups, p_adjust='holm')
print(result)
```

---

## Hypothesis 2: Linear Scaling with Sampling Frequency

### Research Question

> Does the hardware overhead of `perf record` scale **linearly** with sampling frequency (determined by the `-c` flag)? Or does it exhibit non-linear behavior (e.g., super-linear growth due to cache pollution at high sampling rates)?

### Formal Hypotheses

- **H₀:** The hardware overhead of `perf record` does **not** scale linearly with sampling frequency. There are significant non-linear effects (e.g., cache pollution, TLB thrashing) at higher frequencies.

- **H₁:** The overhead scales linearly with sampling frequency. Doubling the sampling rate approximately doubles the profiler's hardware cost.

### Why This Matters

If H₁ is true, we can build a **simple predictive model:** `overhead = β₀ + β₁ × (1/c)`, where `c` is the sampling period. Engineers can plug in their desired sampling rate and predict the overhead.

If H₀ is true (non-linear), we've found something more interesting: a **critical sampling frequency** beyond which the profiler starts causing disproportionate damage to the application's cache performance. This "knee point" would be a **novel, publishable finding** that directly informs production profiling best practices.

### Experiment Design

#### Variables
- **Independent variable:** Sampling period `-c` (continuous — 6 levels: 1K, 10K, 50K, 100K, 500K, 1M)
- **Dependent variable:** Absolute hardware counter values for `perf record` (cycles, cache-misses, branch-misses)
- **Controlled variables:** Workload (`lat_mem_rd`), `-g` flag (ON), CPU pinning, system hardening

#### Configuration Matrix

| Config Label | `-g` | `-c` | Sampling Rate (samples/sec at 2.1 GHz) |
|-------------|------|------|---------------------------------------|
| C1K | ON | 1,000 | ~2,100,000 samples/sec |
| C10K | ON | 10,000 | ~210,000 samples/sec |
| C50K | ON | 50,000 | ~42,000 samples/sec |
| C100K | ON | 100,000 | ~21,000 samples/sec |
| C500K | ON | 500,000 | ~4,200 samples/sec |
| C1M | ON | 1,000,000 | ~2,100 samples/sec |

#### Procedure

1. **Randomized block design:** Do NOT run all iterations of `-c 1K` first, then all of `-c 10K`. Instead, randomize the order within each block:
   ```
   Block 1: C100K → C1K → C1M → C10K → C50K → C500K  (random order)
   Block 2: C50K → C1M → C1K → C500K → C100K → C10K   (different random order)
   ...
   ```
   This prevents temporal confounds (thermal drift, background activity).

2. Insert a 2-second cool-down between each experiment run.

3. Record timestamp for every run (to check for temporal drift in post-analysis).

#### Iterations
- Per configuration: determined by power analysis from pilot (expected ~200–500).
- Total runs: 6 configs × N iterations × 2 (baseline + profiled) = 6 × 400 × 2 = **4,800 runs**.
- Estimated time for `lat_mem_rd` (2s window + 2s cooldown): ~5.3 hours.

#### Repeat on Go API (Selective Validation)
- Pick 3 sampling rates: `-c 10K`, `-c 100K`, `-c 1M` (minimum, midpoint, maximum).
- Use constant-work measurement (1,000 requests per iteration).
- 200 iterations per config.

### Statistical Test

#### Primary Test: Ordinary Least Squares (OLS) Regression

Model: `overhead = β₀ + β₁ × sampling_frequency`

Where `sampling_frequency = 1/c` (higher frequency = smaller `-c` value = more overhead expected).

```python
import numpy as np
from scipy import stats

# sampling_frequency = 1/c for each config
freq = np.array([1/1000, 1/10000, 1/50000, 1/100000, 1/500000, 1/1000000])

# mean overhead at each frequency (from experiments)
overhead_cycles = np.array([...])  # 6 values, one per config

# Linear regression
slope, intercept, r_value, p_value, std_err = stats.linregress(freq, overhead_cycles)
R_squared = r_value ** 2

print(f"Model: overhead = {intercept:.0f} + {slope:.0f} x freq")
print(f"R-squared = {R_squared:.4f}")
print(f"Slope p-value = {p_value:.6f}")
```

#### Testing for Non-Linearity

Fit both linear and quadratic models and compare:

```python
# Linear: y = a + b*x
coeffs_lin = np.polyfit(freq, overhead_cycles, 1)
residuals_lin = overhead_cycles - np.polyval(coeffs_lin, freq)
SS_lin = np.sum(residuals_lin ** 2)

# Quadratic: y = a + b*x + c*x^2
coeffs_quad = np.polyfit(freq, overhead_cycles, 2)
residuals_quad = overhead_cycles - np.polyval(coeffs_quad, freq)
SS_quad = np.sum(residuals_quad ** 2)

# F-test: does the quadratic term significantly improve the fit?
n = len(freq)
F = ((SS_lin - SS_quad) / 1) / (SS_quad / (n - 3))
p_nonlinear = 1 - stats.f.cdf(F, 1, n - 3)

print(f"Quadratic coefficient = {coeffs_quad[0]:.4f}")
print(f"F-test for non-linearity: F={F:.2f}, p={p_nonlinear:.4f}")

if p_nonlinear < 0.05:
    print("SIGNIFICANT non-linearity detected! Cache pollution hypothesis supported.")
else:
    print("Linear model is sufficient. Overhead scales proportionally.")
```

#### Per-Event Regressions

Run separate regressions for each hardware event:

| Event | Expected Behavior | If Non-Linear → Interpretation |
|-------|-------------------|-------------------------------|
| Cycles | Should scale linearly (more NMI handlers = more CPU time) | Super-linear = NMI handlers contending with each other |
| Cache-misses | Most likely to show non-linearity | Super-linear = ring buffer writes evicting workload's L3 cache at high rates |
| Branch-misses | Should scale linearly (profiler has deterministic control flow) | Non-linear would be surprising — investigate |

#### What to Report
- Scatter plot with regression line: X-axis = sampling frequency (1/c), Y-axis = overhead.
- R², slope with 95% CI, p-value for slope.
- Residual plot (to visually check for non-linearity).
- If non-linear: identify the "knee point" where super-linear behavior begins.
- Separate plots for cycles, cache-misses, branch-misses.

#### Expected Outcome
- **Cycles and branch-misses:** Likely linear (R² > 0.95). Each NMI costs a fixed number of cycles.
- **Cache-misses:** May show super-linear growth at high sampling frequencies (`-c 1K`), indicating cache pollution. This would be the most interesting finding.

---

## Hypothesis 3: Call-Graph Recording (`-g`) as the Dominant Cost Component

### Research Question

> Does enabling call-graph recording (`-g` flag) contribute a measurable, separable additional overhead beyond the base cost of sampling? Is it the dominant cost component?

### Formal Hypotheses

- **H₀:** Enabling the `-g` flag does **not** significantly increase the hardware overhead of `perf record` compared to running without it. The call-graph recording cost is negligible.

- **H₁:** The `-g` flag adds a significant, measurable overhead. Call-graph recording (stack walking + symbol resolution) is a major contributor to the total profiling cost.

### Why This Matters

Our ftrace analysis identified `kallsyms_expand_symbol` (kernel symbol resolution during stack unwinding) as a top contributor to `perf record`'s overhead. This function is only invoked when `-g` is enabled — it resolves stack frame addresses to function names. If H₁ is true, we can directly link our ftrace diagnosis to measured overhead.

This also has practical implications: many production profilers run without `-g` to reduce overhead. This experiment quantifies exactly how much overhead `-g` adds, giving engineers data to make that decision.

### Experiment Design

#### Variables
- **Independent variable:** `-g` flag (binary — ON or OFF)
- **Dependent variable:** Absolute hardware counter values for `perf record`
- **Controlled variables:** `-c 100000`, workload (`lat_mem_rd`), CPU pinning, system hardening

#### Configuration Matrix

| Config | `-g` | `-c` | Description |
|--------|------|------|-------------|
| A (with call graph) | ON | 100,000 | Full profiling with stack unwinding |
| B (without call graph) | OFF | 100,000 | Sampling only, no stack capture |

#### Procedure

Use a **paired design**: within each block of iterations, run config A and config B back-to-back, in randomized order.

```
Block 1:  A (with -g) → cooldown → B (without -g) → cooldown
Block 2:  B (without -g) → cooldown → A (with -g) → cooldown
Block 3:  A → cooldown → B → cooldown
...
```

Pairing eliminates between-block variance (thermal changes, background activity) because each A measurement has a temporally adjacent B measurement.

#### Iterations
- Per configuration: determined by power analysis from pilot.
- Expected: 200–500 paired iterations.
- Total runs: 2 × N × 2 (baseline + profiled) = 2 × 400 × 2 = **1,600 runs**.
- Estimated time: ~1.8 hours.

### Statistical Test

#### Primary Test: Paired-Sample t-test

Because the measurements are paired (each with-g run has a corresponding without-g run in the same block), we use a paired t-test which is more powerful than an independent t-test.

```python
from scipy import stats
import numpy as np

# Paired measurements:
# with_g_cycles[i] and without_g_cycles[i] come from the same block
with_g_cycles = np.array([...])     # N values
without_g_cycles = np.array([...])  # N values (paired)

# Paired t-test
t_stat, p_value = stats.ttest_rel(with_g_cycles, without_g_cycles)

# Effect size: Cohen's d for paired samples
diff = with_g_cycles - without_g_cycles
cohens_d = np.mean(diff) / np.std(diff, ddof=1)

print(f"Mean with -g:    {np.mean(with_g_cycles):,.0f} cycles")
print(f"Mean without -g: {np.mean(without_g_cycles):,.0f} cycles")
print(f"Mean difference: {np.mean(diff):,.0f} cycles")
print(f"t = {t_stat:.2f}, p = {p_value:.6f}")
print(f"Cohen's d = {cohens_d:.2f}")

# Interpretation of Cohen's d:
# d < 0.2  -> negligible effect
# d ~ 0.5  -> medium effect
# d > 0.8  -> large effect
```

#### Assumption Check
- **Normality of differences:** Shapiro-Wilk on `diff = with_g - without_g`.
- If non-normal: use Wilcoxon signed-rank test (non-parametric paired test).

```python
# Normality check
stat, p = stats.shapiro(diff)
if p < 0.05:
    # Non-normal -> use Wilcoxon
    stat, p = stats.wilcoxon(with_g_cycles, without_g_cycles)
    print(f"Wilcoxon signed-rank: p={p:.6f}")
```

#### Proportion of Overhead Attributable to `-g`

```python
total_overhead_with_g = np.mean(with_g_cycles)
total_overhead_without_g = np.mean(without_g_cycles)
g_contribution = total_overhead_with_g - total_overhead_without_g
g_percentage = (g_contribution / total_overhead_with_g) * 100

print(f"Call-graph recording contributes {g_percentage:.1f}% of total overhead")
```

#### What to Report
- Mean overhead with and without `-g`, with 95% CIs.
- Mean difference ± 95% CI.
- Cohen's d effect size.
- Percentage of total overhead attributable to call-graph recording.
- Stacked bar chart showing the two cost components (base sampling cost vs. call-graph cost).

#### Expected Outcome
We expect H₁ to hold: `-g` should add significant overhead, primarily from:
- **Stack unwinding:** Walking up the call stack at each sample (~5-20 stack frames).
- **Symbol resolution:** `kallsyms_expand_symbol` converting kernel addresses to names.
- **Additional cache misses:** Reading stack memory that may not be in L1/L2 cache.

We predict `-g` accounts for 30–60% of the total overhead.

---

## Hypothesis 4: Production Negligibility (Equivalence Testing)

### Research Question

> For a production-representative workload (Go API under realistic load), is the total profiling overhead **small enough** to be considered negligible — below a practical threshold of 1%?

### Formal Hypotheses

- **H₀:** The overhead of `perf record -g -c 100000` is **≥ 1%** of the target workload's total hardware resource consumption. Profiling has a practically significant impact on production performance.

- **H₁:** The overhead is **< 1%** of the workload's total resource consumption. Profiling is safe to enable in production with negligible performance impact.

> **Note:** This is the **inverse** of a standard t-test. In standard testing, we try to prove a difference exists. Here, we try to prove the difference is **small enough** to be negligible. This requires a specialized statistical test called TOST (Two One-Sided Tests).

### Why This Matters

This is the **practical punchline** of the entire study. SREs and platform engineers need a yes/no answer: "Can I leave `perf record` running in production without impacting my users?" This hypothesis provides that answer with statistical rigor.

### Choosing the Threshold (delta = 1%)

The choice of delta requires justification. We set delta = 1% based on:
1. **Industry practice:** Google's continuous profiling system (Google-Wide Profiling) targets < 1% overhead. Netflix's profiling infrastructure uses a similar threshold.
2. **SLO budgets:** Most production services have error budgets of 0.1–1%. A profiling tool consuming >= 1% of resources could consume the entire error budget.
3. **Conservative engineering:** 1% is deliberately conservative. If we can prove overhead < 1%, the result is broadly applicable.

> **Discuss with mentor:** "Is 1% the right threshold? Should we also test at 0.5% and 2% to show robustness?"

### Experiment Design

#### Measurement Approach: Constant Work

This hypothesis uses the **constant-work methodology** (per the mentor's recommendation).

- **Fixed work unit:** 1,000 HTTP requests to the Go API.
- **Load generator:** `hey -n 1000 -c 10 http://localhost:8080/compute`
- **Warm-up:** 500 unmeasured requests before each measured iteration.

#### Variables
- **Independent variable:** Presence of `perf record` (binary — baseline vs. profiled)
- **Dependent variable:** Hardware counter values for the **Go API process** (not perf record)
- **Controlled variables:** Request count (1,000), concurrency (10), API configuration, database state

#### Why We Measure the Go API (Not perf record) for H4

For this hypothesis, we flip the measurement target. In H1-H3, we measured `perf record`'s own resource consumption. Here, we measure **the Go API's resource consumption** with and without profiling. The question is: "Does the Go API consume more cycles/cache-misses when it's being profiled?"

This captures the **total impact** including:
- Direct overhead (NMI handler interrupting the Go API).
- Indirect overhead (cache pollution from `perf record`'s ring buffer writes evicting Go API data from L3).

#### Procedure

```
For each iteration i = 1 to N:

    # Warm-up
    hey -n 500 -c 10 http://localhost:8080/compute

    # BASELINE: serve 1,000 requests WITHOUT perf record
    Start perf stat on Go API PID
    hey -n 1000 -c 10 http://localhost:8080/compute
    Stop perf stat -> record baseline_cycles[i], baseline_cache_misses[i]
    
    Cool-down (2 seconds)
    
    # Warm-up
    hey -n 500 -c 10 http://localhost:8080/compute

    # PROFILED: serve 1,000 requests WITH perf record running
    Start perf record -g -c 100000 on Go API PID (pinned to CPU 7)
    Start perf stat on Go API PID
    hey -n 1000 -c 10 http://localhost:8080/compute
    Stop perf stat -> record profiled_cycles[i], profiled_cache_misses[i]
    Stop perf record
    
    Cool-down (2 seconds)
```

#### Iterations
- Determined by power analysis.
- Expected: 200–500 iterations.

### Statistical Test: TOST (Two One-Sided Tests)

#### How TOST Works

Standard t-test: "Is the difference **different** from zero?" (proves existence of effect)
TOST: "Is the difference **within** +/-delta?" (proves smallness of effect)

TOST runs two one-sided t-tests:
1. **Test 1:** H0: mu_diff >= +delta vs H1: mu_diff < +delta (upper bound)
2. **Test 2:** H0: mu_diff <= -delta vs H1: mu_diff > -delta (lower bound)

If **both** reject at alpha = 0.05, we conclude: the true difference lies within (-delta, +delta), i.e., the overhead is negligible.

```python
import numpy as np
from scipy import stats

def tost_test(baseline, profiled, delta_fraction=0.01, alpha=0.05):
    """
    Two One-Sided Tests (TOST) for equivalence.
    
    delta_fraction: equivalence margin as a fraction of baseline mean
                   (0.01 = 1%)
    """
    baseline = np.array(baseline)
    profiled = np.array(profiled)
    
    # Calculate the equivalence margin in absolute terms
    delta = delta_fraction * np.mean(baseline)
    
    # The difference (profiled - baseline)
    diff = profiled - baseline
    mean_diff = np.mean(diff)
    se_diff = stats.sem(diff)
    n = len(diff)
    df = n - 1
    
    # Test 1: H0: mu_diff >= +delta  ->  t1 = (mean_diff - delta) / SE
    t1 = (mean_diff - delta) / se_diff
    p1 = stats.t.cdf(t1, df)  # one-sided: want t1 to be very negative
    
    # Test 2: H0: mu_diff <= -delta  ->  t2 = (mean_diff + delta) / SE
    t2 = (mean_diff + delta) / se_diff
    p2 = 1 - stats.t.cdf(t2, df)  # one-sided: want t2 to be very positive
    
    # TOST p-value is the maximum of the two one-sided p-values
    p_tost = max(p1, p2)
    
    # 90% CI for the difference (TOST uses 90%, not 95%)
    ci_90 = stats.t.interval(0.90, df, loc=mean_diff, scale=se_diff)
    
    print(f"Equivalence margin (delta): +/-{delta:,.0f} cycles (+/-{delta_fraction*100:.1f}%)")
    print(f"Mean difference: {mean_diff:,.0f} cycles")
    print(f"90% CI: ({ci_90[0]:,.0f}, {ci_90[1]:,.0f})")
    print(f"Test 1 (upper bound): t={t1:.2f}, p={p1:.4f}")
    print(f"Test 2 (lower bound): t={t2:.2f}, p={p2:.4f}")
    print(f"TOST p-value: {p_tost:.4f}")
    
    if p_tost < alpha:
        print(f"EQUIVALENT: Overhead is within +/-{delta_fraction*100:.1f}% (p={p_tost:.4f})")
    else:
        print(f"NOT PROVEN EQUIVALENT at {delta_fraction*100:.1f}% threshold")
    
    # Check if the 90% CI falls entirely within (-delta, +delta)
    if ci_90[0] > -delta and ci_90[1] < delta:
        print(f"90% CI ({ci_90[0]:,.0f}, {ci_90[1]:,.0f}) is within +/-{delta:,.0f}")
    
    return p_tost, mean_diff, ci_90

# Usage:
# p, diff, ci = tost_test(baseline_cycles, profiled_cycles, delta_fraction=0.01)
```

#### Per-Request Overhead Claim

After confirming equivalence, compute the per-request overhead:

```python
total_overhead = np.mean(profiled_cycles) - np.mean(baseline_cycles)
per_request = total_overhead / 1000  # 1,000 requests per iteration
per_100_requests = per_request * 100

print(f"Per-request overhead: {per_request:.1f} cycles")
print(f"Per 100 requests: {per_100_requests:,.0f} cycles")
```

This produces the mentor's desired claim format:
> *"For every 100 requests to the Go API, `perf record -g -c 100K` consumed a mean of X CPU cycles as overhead."*

#### What to Report
- Mean baseline and profiled values with 95% CIs.
- Absolute and relative overhead.
- TOST p-value.
- 90% CI for the difference (must fall within +/-delta for equivalence).
- Per-request overhead in cycles.
- A visualization showing the 90% CI relative to the equivalence bounds.

#### Expected Outcome
Based on our preliminary results (0.005% cycle overhead on CPU-bound workload), we strongly expect H₁ to hold. The overhead should be well below 1%.

---

## Cross-Cutting Concerns

### Multiple Comparisons Correction

Across all 4 hypotheses, we run multiple statistical tests. The total test count:

| Hypothesis | Tests | Events Tested | Total |
|-----------|-------|--------------|-------|
| H1 | ANOVA + 3 pairwise post-hocs | cycles, cache-misses, branch-misses | 4 x 3 = 12 |
| H2 | Regression slope significance | cycles, cache-misses, branch-misses | 3 |
| H2 | Non-linearity F-test | cycles, cache-misses, branch-misses | 3 |
| H3 | Paired t-test | cycles, cache-misses, branch-misses | 3 |
| H4 | TOST | cycles, cache-misses | 2 |
| **Total** | | | **~23 tests** |

#### Why This Matters

When you run 1 test at alpha = 0.05, there's a 5% chance of a false positive. With 23 tests:
```
P(at least one false positive) = 1 - (1 - 0.05)^23 = 69%
```
More than two-thirds chance you'll report something as "significant" when it's actually noise.

#### Correction Method: Holm-Bonferroni

Apply Holm-Bonferroni correction across all 23 tests:

```python
from statsmodels.stats.multitest import multipletests

# Collect all p-values from all tests
all_p_values = [...]  # 23 p-values
test_labels = [...]   # 23 descriptive labels

# Holm-Bonferroni correction
reject, adjusted_p, _, _ = multipletests(all_p_values, method='holm')

for label, raw_p, adj_p, rej in zip(test_labels, all_p_values, adjusted_p, reject):
    print(f"{label}: raw p={raw_p:.4f}, adjusted p={adj_p:.4f}, reject={rej}")
```

#### Reporting
Report **both** raw and adjusted p-values in all tables. This demonstrates methodological rigor and allows readers to assess the impact of the correction.

### Sample Size Calculation (Power Analysis)

Before running the full experiment, run a **pilot** of 50 iterations to estimate effect sizes, then compute the required sample size:

```python
from scipy.stats import norm
import math
import numpy as np

def required_sample_size(pilot_data_a, pilot_data_b, alpha=0.05, power=0.80):
    """
    Compute minimum sample size for a two-sample t-test.
    
    pilot_data_a, pilot_data_b: arrays from pilot run (~50 values each)
    """
    mean_a, mean_b = np.mean(pilot_data_a), np.mean(pilot_data_b)
    sd_pooled = np.sqrt((np.var(pilot_data_a, ddof=1) + np.var(pilot_data_b, ddof=1)) / 2)
    
    # Cohen's d from pilot
    cohens_d = abs(mean_a - mean_b) / sd_pooled
    
    # Required n per group
    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    n = 2 * ((z_alpha + z_beta) / cohens_d) ** 2
    
    print(f"Pilot effect size (Cohen's d): {cohens_d:.3f}")
    print(f"Required sample size per group: {math.ceil(n)}")
    print(f"Total runs needed: {math.ceil(n) * 2}")
    
    return math.ceil(n)

# Usage (after pilot):
# n = required_sample_size(pilot_baseline_cycles, pilot_profiled_cycles)
```

### Randomized Experiment Ordering

To prevent temporal confounds (thermal drift, background activity, cron jobs), ALL experiments should be randomized:

```python
import random

# Build the full experiment list
experiments = []
for iteration in range(N_ITERATIONS):
    # H2: all 6 -c configs
    configs_h2 = [("H2", f"-g -c {c}") for c in [1000, 10000, 50000, 100000, 500000, 1000000]]
    random.shuffle(configs_h2)
    experiments.extend([(iteration, *cfg) for cfg in configs_h2])
    
    # H3: with-g and without-g (paired, but order randomized)
    configs_h3 = [("H3", "-g -c 100000"), ("H3", "-c 100000")]
    random.shuffle(configs_h3)
    experiments.extend([(iteration, *cfg) for cfg in configs_h3])

# experiments is now the full randomized run order
```

### Constant Work Methodology (Go API)

For all Go API experiments (H1 with Go API, H4), use constant-work measurement:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Requests per iteration | 1,000 | Long enough to amortize startup noise, short enough for many iterations |
| Concurrency | 10 VUs | Realistic concurrent load pattern |
| Warm-up requests | 500 | Primes JIT, connection pools, page cache |
| Cool-down between iterations | 2 seconds | Allows caches and CPU temperature to stabilize |
| Load tool | `hey` or k6 with `--iterations` | Fixed request count, exits when done |

### Experiment Session Recording

At the start of each experiment session, save system state:

```bash
#!/bin/bash
# record_system_state.sh — run BEFORE experiments
SESSION_ID=$(date +%Y%m%d_%H%M%S)
mkdir -p results/session_${SESSION_ID}

lscpu > results/session_${SESSION_ID}/lscpu.txt
uname -a > results/session_${SESSION_ID}/uname.txt
cat /proc/cmdline > results/session_${SESSION_ID}/cmdline.txt
dmesg | tail -100 > results/session_${SESSION_ID}/dmesg.txt
numactl --hardware > results/session_${SESSION_ID}/numa.txt
cat /sys/devices/system/cpu/intel_pstate/no_turbo > results/session_${SESSION_ID}/turbo.txt
cat /proc/sys/kernel/randomize_va_space > results/session_${SESSION_ID}/aslr.txt
cat /sys/kernel/mm/transparent_hugepage/enabled > results/session_${SESSION_ID}/thp.txt
git log -1 --format="%H %s" > results/session_${SESSION_ID}/git_commit.txt

echo "System state recorded for session ${SESSION_ID}"
```

### Data Output Format

All experiments output to a unified CSV format:

```csv
session_id,timestamp,hypothesis,workload,config_g,config_c,iteration,run_type,cycles,cache_misses,branch_misses,page_faults,context_switches,requests_served,duration_ms
20260714_1400,2026-07-14T14:01:23,H1,lat_mem_rd,on,100000,1,baseline,10456000000,234000,12000,45,3,NA,2000
20260714_1400,2026-07-14T14:01:28,H1,lat_mem_rd,on,100000,1,profiled,10456500000,242000,12100,47,5,NA,2000
20260714_1400,2026-07-14T14:01:33,H1,goapi,on,100000,1,baseline,8210000000,189000,9800,120,45,1000,12340
20260714_1400,2026-07-14T14:01:50,H1,goapi,on,100000,1,profiled,8210520000,195000,9850,122,48,1000,12380
```

---

## Expected Results Summary

| Hypothesis | Expected Outcome | Key Metric |
|-----------|-----------------|------------|
| H1 | Constant absolute cost (~500K cycles) across workloads | ANOVA p > 0.05 + TOST equivalence |
| H2 | Linear scaling (R-squared > 0.95) for cycles; possible non-linearity for cache-misses at `-c 1K` | R-squared, regression slope, F-test for quadratic term |
| H3 | `-g` adds ~30-60% of total overhead (call-graph = dominant component) | Cohen's d > 0.8 (large effect), paired t-test p < 0.001 |
| H4 | Total overhead < 1% for Go API under realistic load | TOST p < 0.05, 90% CI within +/-1% |

### The Narrative (If All Hold)

> *"We found that `perf record`'s hardware overhead is a **fixed, predictable cost** (H1) of approximately 500K CPU cycles per 2-second measurement window, regardless of workload type. This cost **scales linearly** with sampling frequency (H2, R-squared = 0.97), enabling a simple predictive model: `overhead = 24 cycles x samples_per_second`. The **dominant cost component** is call-graph recording via the `-g` flag (H3), which accounts for 45% of total overhead through stack unwinding and symbol resolution — directly confirmed by our ftrace diagnosis showing `kallsyms_expand_symbol` as the top kernel function. For a production Go API serving 1,000 requests, the total overhead is **52 cycles per request** (H4), well below the 1% threshold (p < 0.001), confirming that `perf record` is safe for continuous production profiling."*

---

## Timeline

| Week | Tasks | Hypotheses |
|------|-------|-----------|
| 1, Days 1-2 | System hardening, NUMA documentation, install `lmbench` | Setup |
| 1, Days 3-4 | Pilot runs (50 iterations each), power analysis, sample size calculation | All |
| 1, Day 5 | Build unified automation script with randomized ordering | All |
| 2, Days 1-2 | H2 + H3 on `lat_mem_rd` (ablation study — runs overnight) | H2, H3 |
| 2, Days 3-4 | H1 on `lat_mem_rd` + Go API (constant work) | H1 |
| 2, Day 5 | H4 on Go API + H2 selective validation (3 configs on Go API) | H4, H2 |
| 3, Days 1-3 | Full statistical analysis: ANOVA, regression, TOST, correction | All |
| 3, Days 4-5 | Write-up: tables, figures, narrative | All |
