# MEETING BRIEF — Guide Check-In on Meta-Profiling Project

**Date:** September 21, 2026  
**Project:** Overhead Analysis of Linux Performance Observability Tools (`perf record`, eBPF/bpftrace)  
**Lab:** CDSAML & CCBD, PES University  
**Guide:** Dr. Prafullata Kiran Auradkar  
**Alumni Mentor:** Mr. Parth Shah (Tesla, Sr. Performance Engineer)  
**Team:** INT_22 (P Rishikesh, N Parthiv Kumar, P Vishnupranav Reddy, Pavan K Aithal)  
**Last commit:** July 20, 2026 — **~9 weeks ago**  
**Meeting goal:** Define a realistic path to a concrete end deliverable.

---

## 1. Where We Are (30-Second Recap)

**Problem:** Observability tools (`perf`, eBPF) impose hidden hardware overhead on applications they monitor. Nobody measures the monitor itself.

**What we built:** A three-layer measurement architecture on a dual-socket Intel Xeon E5-2620 v4 (32 vCPUs, Broadwell-EP):
- **Layer 1 (Target):** `lat_mem_rd` memory benchmark OR a Go REST microservice under HTTP load
- **Layer 2 (Profiler under study):** `perf record -g -c <period>` pinned to an isolated CPU core
- **Layer 3 (Ruler):** `bpftrace` eBPF probes on a separate NUMA socket to count the profiler's own hardware events without polluting its caches

**What we actually ran and have data for:**
- ✅ **1,000-iteration dummy workload sweep** (H1, H2, H3) — 11,997 rows of CSV data collected July 15–16
- ✅ **Pilot 50-iteration results** with summary tables (H1–H4)
- ✅ **ftrace kernel function graph** of perf record's internal execution path (top 30 hottest functions)
- ✅ **eBPF kprobe latency histograms** of `kallsyms_expand_symbol`, `perf_mmap_alloc_page`, `__perf_event_overflow`
- ✅ **Go API microservice** (Dockerized Go REST + Postgres + Nginx + Prometheus + Grafana)
- ⚠️ **Go API hypothesis testing** — scripts exist and were iterated on, but only early/small result CSVs (5 summary rows each, not raw iteration data). No full 200+ iteration Go API run was completed.
- ❌ **No final unified analysis report** — `analyze_hypotheses.py` was never run on both CSVs together
- ❌ **No publication draft or formal writeup** beyond the internship slides (16 slides, image-heavy)

**Slides vs. reality cross-check:**
- Slides present the architecture, hypotheses, and results tables accurately — the numbers in the slides match the pilot data files.
- Slides do NOT mention the 9 verified code bugs documented in `analysis_results.md` (PID churn, PMU contention, NMI observer effect, unisolated HyperThread siblings, etc.).
- Slides do NOT present Go API (H4 production) results — those were never fully collected.

---

## 2. What We Have Learned So Far

All numbers below are from **verified data files**. Marked *preliminary* where sample sizes or known bugs limit confidence.

### Hard numbers from 1,000-iteration dummy workload run:

| Finding | Number | Source |
|---------|--------|--------|
| perf record cycle cost at `-c 100K -g` | **2.17M cycles / 5s window** (σ = 1.74M) | H1 tool_overhead, n=1000 |
| Workload baseline cycles | **10.01B cycles / 5s window** | H1 workload_baseline, n=999 |
| **Overhead ratio (cycles)** | **0.0217%** | tool/workload |
| `-g` ON vs OFF cycle difference | +1.04M cycles (**+99.2% inflation**) | H3, n=1000 per group |
| `-g` accounts for this % of total tool cost | **~49.8% of profiler runtime** | 1.04M / 2.08M |
| Overhead at `-c 1K` (most aggressive) | 3.71M cycles | H2, n=1000 |
| Overhead at `-c 1M` (least aggressive) | 348K cycles | H2, n=1000 |
| Linear scaling (H2)? | **Rejected** — F-test p < 0.001, overhead plateaus at high sampling rates | H2 regression |

### Preliminary observations (pilot, n=50):
- Cycle overhead confirmed < 1% by TOST equivalence test (p < 0.0001)
- Cache-miss and context-switch TOST tests *failed* — overhead ratio for context switches is 19.33% (relative to a workload that does almost none)
- Top kernel functions in perf record: `seq_printf` (217ms cumulative), `kallsyms_expand_symbol` (142ms), `perf_mmap_alloc_page` (19ms)
- NMI handler latency: mode at 2–4 μs per overflow event

### What we do NOT have numbers for:
- Go API overhead under realistic production load (full run never completed)
- Cross-workload comparison (dummy vs Go API on same metrics)
- Effect of fixing the 9 code bugs on results

---

## 3. Open Questions and Gaps

### Methodological gaps:
1. **HyperThread siblings not isolated.** CPUs 22, 23, 30 (siblings of our isolated cores) were left in the scheduler pool. OS threads can pollute L1/L2 caches. This adds noise to baselines. Fix: update `isolcpus` or disable HT.
2. **NMI observer effect not quantified.** Our eBPF ruler generates NMIs on the target CPU. At 10K period, that's ~210K NMIs/sec, inflating perf record's measured cycles by 84–147M cycles/sec. We never measured how much of our reported overhead is *our own ruler's footprint*.
3. **Go API experiment incomplete.** H4 ("production negligibility") was designed for the Go API workload but never executed at scale. We can only make the negligibility claim for synthetic compute-bound workloads.
4. **Only one profiler studied.** The project title says "observability tools" (plural), but we only measured `perf record`. No data on eBPF-based profilers (as the *subject* of measurement), Prometheus exporters, OpenTelemetry agents, or `/proc`-based monitors.
5. **No latency/throughput impact measured.** We measured the profiler's *own* hardware footprint but never measured whether it degrades the *application's* end-to-end response time or throughput (the user-visible impact).

### Slides claims without supporting evidence:
- Literature survey slides (5–7) reference prior work but the reference list is image-based — I cannot verify the citations. **Need to confirm with team.**
- Scope slide implies comparing "multiple tools." Only `perf record` was studied.

---

## 4. Candidate End Goals

### Option A: Polish & Complete the Current `perf record` Study
**Research question:** What is the exact, causally decomposed hardware overhead of `perf record` across workloads, and is it production-negligible?

**Final deliverable:** A 6–8 page technical report or poster with:
- Fixed methodology (isolate HT siblings, quantify ruler observer effect)
- Full 200+ iteration Go API run
- Unified H1–H4 analysis report from `analyze_hypotheses.py`
- Overhead-vs-sampling-rate curve (H2 plot)
- Call-graph cost attribution table (H3)
- TOST negligibility proof for production workload (H4)

**Remaining work:**
- Fix GRUB config (HT siblings) — 1 hour + reboot
- Run Go API hypothesis test — ~4 hours unattended machine time
- Run unified analysis script — 30 min
- Write report — 6–10 hours
- **Total:** ~15–20 hours over 2–3 weeks

**Verdict:** Lowest risk. Uses existing infrastructure. Produces a clean, self-contained result.

---

### Option B: Comparative Overhead of Multiple Linux Profilers
**Research question:** How do `perf record`, continuous eBPF profilers (e.g., `pprof` via BPF, Pyroscope agent), and `/proc`-polling monitors compare in hardware overhead?

**Final deliverable:** A comparative overhead table and radar chart across tools, with a practitioner recommendation ("use X for production, Y for debugging").

**Remaining work:**
- Instrument 2–3 additional tools using the existing Layer 3 architecture
- Design fair comparison (same workload, same sampling rate where applicable)
- Collect data + analyze — 30–40 hours
- Write comparative report — 8–12 hours
- **Total:** ~40–55 hours over 4–6 weeks

**Verdict:** Higher impact but significantly more work. Risk of scope creep if tools behave very differently.

---

### Option C: Overhead vs. Application-Visible Impact (Latency Study)
**Research question:** Does the 0.02% hardware overhead from `perf record` actually translate to measurable p99 latency degradation in a web service?

**Final deliverable:** Latency distribution comparison (CDF/histogram) showing p50, p95, p99 with and without profiler, plus a "latency tax per profiler configuration" table.

**Remaining work:**
- Modify Go API load tests to capture per-request latency distributions
- Run baseline vs profiled latency tests at multiple load levels
- Statistical comparison (K-S test or similar)
- **Total:** ~25–35 hours over 3–4 weeks

**Verdict:** Very practical and publishable. Answers what engineers actually care about. Builds on existing Go API setup.

---

### Option D: Configuration Recipe / Decision Framework
**Research question:** Given a production environment, what is the optimal `perf record` configuration (`-c` value, with/without `-g`) that balances profiling fidelity against overhead?

**Final deliverable:** A one-page decision tree or lookup table: "If your workload is CPU-bound and you need call graphs, use `-c 100K -g` (0.02% overhead). If you need minimal impact, use `-c 1M` without `-g` (0.003% overhead)." Plus supporting data.

**Remaining work:**
- Already have most of the data (H2 + H3 provide the trade-off curves)
- Need Go API validation
- Package into a clean recommendation document
- **Total:** ~12–18 hours over 2 weeks

**Verdict:** Least novel but most immediately useful. Could be a companion output to Option A.

---

## 5. How Overhead Analysis Is Typically Done

Standard methodology for measuring instrumentation overhead in systems research:

### What to measure:
| Metric Category | Specific Metrics | Why |
|----------------|-----------------|-----|
| **CPU** | Cycles consumed, instructions retired, IPC impact | Direct compute cost |
| **Memory** | Cache misses (L1/L2/L3), memory bandwidth, RSS | Cache pollution / memory pressure |
| **Latency** | Application p50/p95/p99 response time WITH vs WITHOUT tool | User-visible impact |
| **Throughput** | Requests/sec or operations/sec degradation | Capacity impact |
| **I/O** | Disk writes (perf.data files), network overhead (for remote exporters) | Storage/bandwidth cost |
| **Kernel** | Context switches, NMIs, syscalls injected by the tool | Kernel-level disruption |

### How to design fair experiments:
1. **Isolated environment:** Dedicated cores, NUMA isolation, disabled turbo boost/ASLR/THP — **we do this well.**
2. **Baseline vs. treatment:** Measure the same workload with and without the tool — **we do this.**
3. **Multiple iteration averaging:** n ≥ 30 minimum, n ≥ 200 for tight CIs — **we have n=1000 for dummy.**
4. **Warmup periods:** Discard initial measurements to avoid cold-cache effects — **we do this for Go API.**
5. **Randomized block design:** Shuffle treatment order to avoid time-of-day bias — **we do this for H2/H3.**
6. **Ruler overhead quantification:** Measure the measurement tool's own cost — **we identify this issue but don't quantify it.**
7. **Workload diversity:** Test across CPU-bound, memory-bound, I/O-bound workloads — **we have 2 of 3 (missing I/O-pure).**
8. **Statistical rigor:** Use appropriate tests (Welch's t for means, TOST for equivalence, effect sizes) — **we do this.**

### What we're missing vs. standard practice:
- **No application-visible latency measurement** (most papers include p99 impact)
- **No multi-tool comparison** (most overhead papers compare ≥ 3 tools)
- **No ruler overhead quantification** (we know the issue but never bounded it)
- **Narrow workload coverage** (only CPU-bound and one web service)

*Note: Key related work includes Gregg (2019) "BPF Performance Tools", overhead sections in Ren et al. on continuous profiling costs, and the general perf documentation. Verify specific citations before using.*

---

## 6. Recommended Direction

**Go with Option A (Polish Current Study) + Option D (Configuration Recipe) as a combined deliverable.**

Rationale:
- You have 1,000 iterations of clean data already. The hard experimental work is 80% done.
- The remaining work is mostly *running existing scripts* (Go API test) and *fixing a GRUB config* — not writing new code.
- Combining A + D gives you both a rigorous scientific output AND a practical practitioner-facing output.
- Total effort: ~20–25 hours, achievable in 3 weeks at 8–10 hours/week.
- If the guide wants more ambition, Option C (latency study) is the natural extension and reuses the same infrastructure.

---

## 7. Questions to Ask Your Guide

### Direction & scope:
1. "What end deliverable does the lab expect — a technical report, a poster, a workshop paper submission, or an internal document?"
2. "Should we scope to just `perf record`, or do you want us to include at least one other tool (e.g., an eBPF continuous profiler)?"
3. "Is the Go API experiment essential, or is the dummy workload data sufficient for a first deliverable?"

### Success criteria:
4. "What would you consider a successful completion? A specific table/figure? A statistical threshold?"
5. "Is there a target venue or deadline (conference, journal, lab report deadline)?"

### Resources & logistics:
6. "Do we still have access to the Xeon testbed (worker-3)? I need to reboot it with updated GRUB parameters."
7. "Should we coordinate with the alumni mentor (Parth Shah) again, or is this now between us?"

### Timeline:
8. "I can commit X hours/week starting now. Is a 3–4 week timeline for a polished report realistic from your perspective?"
9. "Would a mid-point check-in in 1.5 weeks be helpful?"

---

## 8. Talking Points (5–7 Minute Flow)

### Opening (30 sec):
> "Thank you for meeting. I want to be upfront — I paused this project in late July to prepare for campus placements, and I'm resuming now. Today I want to recap where we are, identify what's missing, and get your input on what the final deliverable should look like."

### Recap (90 sec):
> "We built a three-layer meta-profiling architecture to measure the hardware overhead of `perf record` in isolation. The key insight is using eBPF probes on a separate NUMA socket as an independent ruler. We ran 1,000 iterations on a synthetic workload and found that:
> - `perf record` at default settings costs about 2.2 million cycles per 5-second window — that's 0.02% of the workload's own cycles.
> - Call-graph unwinding (`-g`) doubles the profiler's cost. It accounts for roughly half of all overhead.
> - The overhead doesn't scale linearly with sampling frequency — it plateaus at high rates due to kernel NMI throttling.
> These numbers come from verified data. We also have pilot results from the Go API but never completed a full run."

### Gaps (60 sec):
> "There are three main gaps. First, we only tested `perf record` — the project scope implied multiple tools. Second, we never ran the Go API at full scale, so the production-negligibility claim is only proven for synthetic workloads. Third, our code review surfaced 9 issues — the most important being that our HyperThread siblings weren't isolated, which adds noise to baselines. None of these are fatal, but they need acknowledgment or fixing."

### Proposal (90 sec):
> "I see two realistic paths given my time:
> **Path 1:** Polish what we have. Fix the HT isolation, run the Go API test, produce a unified analysis report and a configuration recommendation table. About 20 hours of work, 3 weeks.
> **Path 2:** Same as Path 1, plus measure application-visible latency impact (p99 response time). Adds another 10–15 hours but answers what engineers really care about.
> I'd lean toward Path 1 with a stretch goal of Path 2 if time allows."

### Ask (60 sec):
> "What I need from you:
> 1. Which of these paths aligns with what the lab expects?
> 2. Is there a deadline or target venue?
> 3. Do we still have access to the testbed?
> I'm ready to start this week."

---

## 9. Likely Questions from the Guide — with Suggested Answers

| Question | Suggested Answer |
|----------|-----------------|
| "Why did you stop for 9 weeks?" | "Campus placement preparation. I should have communicated that earlier — I apologize for the gap." |
| "What's the state of the code?" | "Functional. We have working measurement scripts, 12K rows of raw data, and a statistical analysis pipeline. A code review identified 9 issues, most of which have fixes already written but not yet validated with new runs." |
| "Can we still publish this?" | "Yes, if we scope it right. The perf record overhead characterization with the three-layer architecture is novel enough for a workshop paper or poster. A comparative multi-tool study would be stronger but requires more time." |
| "What about the other team members?" | "I should check in with them. The meeting today is about technical direction — I'll coordinate with the team on task distribution after we agree on the goal." |
| "How confident are you in the numbers?" | "The 1,000-iteration data is robust for relative comparisons (H2, H3). The absolute overhead numbers may be inflated by 5–10% due to the ruler's own NMI observer effect, which we documented but haven't subtracted. All claims in the slides match the data files." |
| "Why only perf record? The title says observability tools." | "Scoping decision during the internship — perf record was the most complex case to instrument correctly. Adding another tool (e.g., a BPF profiler) is feasible with the existing architecture if that's the priority." |
| "Do you need the testbed again?" | "Yes, for about 8 hours of unattended run time (Go API experiment), plus a reboot to fix GRUB isolation parameters." |

---

## 10. Pre-Meeting Checklist (Next Hour)

- [ ] **Re-read** the pilot results table: [`perf_record_overhead_pilot_dummy.md`](file:///c:/Users/vishn/OneDrive/Desktop/Performance_Analysis/Meta-Profiling/perf_record_overhead_pilot_dummy.md) — this is your cheat sheet for numbers.
- [ ] **Skim** the 9-issue code review: [`analysis_results.md`](file:///c:/Users/vishn/OneDrive/Desktop/Performance_Analysis/Meta-Profiling/analysis_results.md) — know issues 1, 2, 3, 5 cold.
- [ ] **Open** the slides: [`CDSAML_internship_template.pptx`](file:///c:/Users/vishn/OneDrive/Desktop/Performance_Analysis/Meta-Profiling/CDSAML_internship_template.pptx) — flip through to refresh your memory of what was presented.
- [ ] **Glance at** the NUMA diagram: [`numa_topology_analysis.md`](file:///c:/Users/vishn/OneDrive/Desktop/Performance_Analysis/Meta-Profiling/numa_topology_analysis.md) — be ready to sketch the 3-layer architecture on a whiteboard.
- [ ] **Check** testbed access: Can you still SSH into worker-3? Don't need to run anything, just confirm.
- [ ] **Have this document open** on your laptop during the meeting.

---

## Top 3 Things You Must Not Forget to Say

1. **"I paused the work in late July and I'm restarting now."** — Say it in the first 30 seconds. Don't let the guide discover it. Own it.

2. **"We have 1,000 iterations of clean data showing 0.02% cycle overhead and a 99% cost increase from call-graph recording."** — These are your strongest concrete numbers. Lead with them.

3. **"What does the lab need this to be — a report, a poster, a paper? That determines what I do next."** — This is the single most important question. Everything else follows from the answer.
