# Next Steps: Meta-Profiling → Publication

Hi Ma'am,

Here's our plan to take the current draft to a publishable paper. We've identified what's missing and broken it into clear phases.

---

## Where We Are Now

- The **methodology and paper structure are solid** — the 3-layer isolation architecture, the 4-hypothesis framework, and the statistical approach (TOST equivalence testing) are all well-positioned against prior work.
- However, the current results are from a **small pilot run (50 iterations) on only one workload** (the synthetic dummy). This is the main gap — we haven't yet validated our claims on the Go microservice, and the sample sizes are too small for a conference submission.

---

## What We Plan To Do

### Step 1: Fix the Measurement Pipeline (1 week)

Our current measurement setup uses eBPF-based counting, which introduces some known accuracy issues (rounding errors, interference between measurement tools). We plan to:

- **Switch to `perf stat`** as our primary measurement tool — it gives exact hardware counter readings with zero approximation error.
- **Fix known bugs** in our automation scripts (e.g., one analysis script collects Go API data but never actually processes it).
- **Tighten CPU isolation** — isolate the HyperThread sibling cores to eliminate stray OS interference on our measurement CPUs.

### Step 2: Run Full-Scale Experiments (2 weeks)

Once the pipeline is clean, we'll run all four hypotheses with proper sample sizes:

| Experiment | What We're Testing | Workload | Iterations |
|-----------|-------------------|----------|-----------|
| H1 | Is the profiler's cost the same regardless of workload? | Dummy **+ Go API** | 500 each |
| H2 | How does overhead change with sampling frequency? | Dummy (+ 3 configs on Go API) | 200 per config |
| H3 | How much does call-graph recording (`-g`) add? | Dummy | 500 paired |
| H4 | Is total overhead < 1% on a real application? | **Go API** (1000 requests/iteration) | 500 paired |

The key addition is the **Go API experiments** — this is what lets us make the "safe for production" claim with real data instead of just projecting from a synthetic benchmark.

### Step 3: Statistical Analysis & Figures (1 week)

- Run all planned statistical tests (Welch's ANOVA for cross-workload comparison, TOST for production negligibility, paired tests for the ablation study).
- Apply multiple-comparison correction (Holm-Bonferroni) across all tests.
- Generate publication-quality figures: scaling curves, ablation bar charts, equivalence plots.

### Step 4: Rewrite the Paper (2 weeks)

- Replace pilot results with full results across both workloads.
- Strengthen the conclusion — currently 3 of 4 hypotheses say "future work"; after Step 2, most should be fully supported.
- Compute a concrete per-request overhead number for the Go API (e.g., *"X additional CPU cycles per 100 API requests"*).

### Step 5: Submit (1 week)

- Prepare a reproducibility artifact (scripts + instructions for reviewers to replicate our experiments).
- Final review and formatting for the target venue.

---

## Target Venues

We're looking at these conferences (all good fits for measurement methodology + profiling overhead):

| Venue | Typical Deadline | Paper Length |
|-------|-----------------|-------------|
| **ICPE** (ACM/SPEC Performance Engineering) | Nov–Dec | 10 pages |
| **ISPASS** (IEEE Performance Analysis) | Dec–Jan | 10 pages |
| **CHEOPS** (Workshop on Performance Methods) | Varies | 4–6 pages |

**ICPE or ISPASS** would be the strongest target. If we want something faster with a lower bar, **CHEOPS** (workshop paper) is an option.

---

## Total Timeline: ~7–8 weeks

We'd appreciate your guidance on:
1. Should we aim for a **full conference paper** (ICPE/ISPASS) or a **workshop paper** (CHEOPS) first?
2. Should we add a third workload (e.g., an I/O-heavy benchmark) to strengthen the cross-workload claim, or are two workloads sufficient?

Thank you!
