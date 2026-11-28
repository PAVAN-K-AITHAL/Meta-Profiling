# Experiment plan — from pilot to a paper draft (Nov 28 → mid-Dec 2026)

## Why the plan changed

Three problems in the current state make the existing results unusable for a paper:

1. **The overhead measurement missed most of the cost.** Every number so far is
   `perf stat -p <perf record PID>`: perf record's own process on CPU 7. The expensive
   part of sampling, the PMU interrupt and the callchain unwinding, runs on **CPU 6,
   inside the workload**, and was never measured. On top of that, Intel's PMU interrupt
   handler switches every counter off while it runs, so even a counter on CPU 6 cannot
   see the handler's own cycles.
2. **The Go API runs never did any work.** `/compute` only accepts POST; `hey` sent GET,
   got `405 Method Not Allowed` in microseconds, and still exited 0. Every Go API number
   so far measured 405 responses. (Fixed: `hey -m POST`, plus a check that refuses to run
   unless the API returns 200.)
3. **The draft's results came from the old, noisy setup.** The Nov 24 pilot (steady
   workload, CV ≈ 1%) contradicts them: 0.22% instead of 0.016%, and a cliff at `-c 5K`
   instead of a knee at 100K–500K.

## The new measurement (two-sided)

Each profiled 5 s window is counted from both sides at the same time:

| Row | What is counted | Answers |
|---|---|---|
| `workload_baseline` | CPU 6, no profiler | reference |
| `workload_profiled` | CPU 6 while perf record samples it | **what profiling costs the program** |
| `tool_overhead` | perf record's PID (CPU 7) | what the profiler process costs the machine |

The window length is fixed and the workload never stops, so profiling cost shows up as
**lost work**: `slowdown = 1 − instructions:u(profiled) / instructions:u(baseline)`.
It is decomposed into:

- **time stolen** — user cycles lost to perf
- **IPC loss** — user code running slower afterwards (cache/TLB/branch pollution)
- **kernel cycles added** — perf's kernel work the PMU can see
- **uncounted cycles** — `(u+k)` cycles that vanished: time inside the PMU interrupt
  handler, where every counter is off

That blind spot is itself a methodological finding: any study that measures profiler
overhead with hardware counters underestimates it.

For the Go API (constant work), the same blind spot is handled by recording
`task-clock` (CPU time) and `duration_time` (wall time) next to `cycles`.

## Revised hypotheses

| | Old | New |
|---|---|---|
| H1 | perf record's cost is constant across workloads (nearly guaranteed by the setup: same `-c`, same window ⇒ same sample count) | **H1W:** per-sample cost depends on the workload — on call-stack depth (cycles per unwound frame) and not on memory behaviour |
| H2 | cost is linear in sampling rate | cost is ~linear in samples on CPU 6, perf record's own cost is sub-linear, and there is a **cliff** where perf record saturates a core |
| H3 | `-g` roughly doubles the cost | cost of each call-graph mode: none / frame pointer / LBR / DWARF, on a shallow and a deep stack |
| H4 | < 1% on the dummy workload (perf record's PID only) | < 1% **slowdown of a real service** (Go API CPU time, TOST) at `-c 100K`, and how far it rises at `-c 10K` |

## Phases (server)

Everything runs through one script; each phase writes its own CSV, and a finished phase
is never re-run, so a crash loses at most one phase.

```
cd ~/Meta-Profiling && git pull
cd meta_profiling_deliverables
sudo bash common/system_harden.sh            # turbo off, THP off, ASLR off, IRQs moved
sudo bash run_paper_experiments.sh check     # ~1 min
sudo bash run_paper_experiments.sh pilot     # ~10 min → results/paper_*/pilot/report.md
```

**Stop here and check the pilot report** (or send it to Claude):
- no `REJECTED: counters multiplexed` lines in the log (if there are: remove
  `L1-dcache-load-misses` from `PROFILED_EVENTS` in `run_hypotheses_dummy.py`)
- slowdown at `-c 100K` is positive and stable across the 10 iterations
- `uncounted` cycles are > 0 (confirms the PMU blind spot); if they are ≈ 0, the cost is
  in `kernel cycles` instead, and the paper text changes accordingly
- the Go API check printed `20/20 ... returned 200`

Then the full campaign, overnight (~8 h). Use `tmux` so a dropped SSH session doesn't
kill it:

```
tmux new -s exp
sudo bash run_paper_experiments.sh all
# detach: Ctrl-b d     re-attach: tmux attach -t exp
# after a crash: sudo bash run_paper_experiments.sh --session paper_<timestamp> all
```

| Phase | What | Size | Time |
|---|---|---|---|
| `h1` | `-g -c 100K` on dummy_workload | 100 iters | ~35 min |
| `h2` | 8 periods 5K…1M, randomized blocks | 50 blocks | ~1 h 45 min |
| `h3` | none / fp / LBR / DWARF on dummy_workload | 50 blocks | ~1 h |
| `h3deep` | same on synth_wl, stack depth 64 | 50 blocks | ~1 h |
| `h1w` | dummy + synth_wl depth {1,16,64,120} × working set {1, 256 MB} | 30 blocks | ~1 h 30 min |
| `goapi` | perf record's cost + H4 at `-c 100K` and `10K` (POST, constant work) | 100 iters | ~1 h 30 min |
| `analyze` | `report.md` + figures for the whole session | | 1 min |

Then commit the session folder (`results/paper_<timestamp>/`) and push.

## After the data (Claude + team)

1. **Read the report** and decide each hypothesis. Re-run any phase with wide CIs.
2. **Optional follow-up:** re-run the ftrace attribution (`ftrace_capture.sh`) to see where
   inside the interrupt handler the time goes (callchain copy vs. sample output). Only if
   time allows; the old "30–60% in kallsyms" claim stays out of the paper unless
   re-measured.
3. **Rewrite the draft:**
   - Methodology: add the two-sided measurement and the PMU blind spot
   - Workloads: describe dummy_workload and synth_wl as they are (the draft calls the
     main workload `lat_mem_rd`, which is wrong)
   - Results: replace every table; new figures from `analyze_v2.py`
   - Add an "old vs new method" comparison: the same setup measured the PID way gives
     a ~10× smaller number. This is direct evidence for the paper's thesis.
   - Threats to validity: one machine (Broadwell), one kernel, PMI behaviour is
     Intel-specific, Go API not pinned
4. **Mentor review** of the new draft.

## Timeline

| Date | Step |
|---|---|
| Sat Nov 28 | Push code, run `check` + `pilot` on the server, review pilot report |
| Sat–Sun night | Full campaign (`all`, ~8 h) |
| Mon Nov 30 | Commit results, analysis, fix/re-run weak phases |
| Dec 1–2 | Optional ftrace follow-up; outline new results section |
| Dec 3–5 | New draft (methodology, results, figures, abstract) |
| Dec 6–7 | Mentor review |
| Dec 11 / Dec 18 | ISPASS 2027 abstract / paper — if the mentor agrees it's ready |
| fallback: Feb 1, 2027 | ICPE 2027 Emerging Research track (6 pages, work-in-progress) |

## Files

| File | Role |
|---|---|
| `meta_profiling_deliverables/run_paper_experiments.sh` | runs the phases above |
| `meta_profiling_deliverables/dummy_workload_test/run_hypotheses_dummy.py` | H1, H1W, H2, H3 (two-sided) |
| `meta_profiling_deliverables/dummy_workload_test/synth_wl.c` | workload with controlled stack depth and working set |
| `meta_profiling_deliverables/go_api_test/run_hypotheses_goapi.py` | Go API, constant work, POST |
| `meta_profiling_deliverables/common/analyze_v2.py` | report + figures for the new data |
| `meta_profiling_deliverables/common/analyze_hypotheses.py` | old analysis, only for the old CSVs |
