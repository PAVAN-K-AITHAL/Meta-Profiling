#!/usr/bin/env python3
"""
run_hypotheses_dummy.py — Unified Hypothesis Testing for CPU-Bound Workload

Measurement methodology:
  perf stat (counting mode, exact PMU register reads) measures perf
  record's OWN PID to count the hardware events (cycles, L1-dcache
  misses, branch-misses) consumed by the profiling tool itself.  This is
  the "instrumentation of instrumentation" approach — a non-sampling
  outer ruler counting the inner tool's overhead.  bpftrace is kept for
  diagnostics only (see common/ebpf_counter*.bt).

Per-iteration flow:
  1. [Workload baseline] perf stat -C 6 (no profiler)
  2. Start perf record (inner tool under study) on CPU 7
  3. [Tool overhead] perf stat -p <perf record PID> for DURATION seconds
  4. Stop perf record
  5. Record both measurements to CSV

Hypotheses tested:
  H1: Constant absolute cost — same perf record cost across workloads
  H2: Linear scaling — tool cost vs sampling frequency (1/c)
  H3: -g ablation — call-graph recording cost component

Data format:
  Each CSV row has run_type = "workload_baseline" or "tool_overhead".
  For H1/H2/H3: analyze tool_overhead rows directly (perf record's cost).
  For H4 (ratio): tool_overhead / workload_baseline × 100%.

CPU pinning (Intel Xeon E5-2620 v4, isolated: 6,7,14,15):
  Workload:               CPU 6  (Core 6, Socket 0)
  perf record:            CPU 7  (Core 7, Socket 0)
  perf stat (ruler):      CPU 14 (Core 6, Socket 1)

Usage:
  sudo python3 run_hypotheses_dummy.py --pilot            # 50 iterations
  sudo python3 run_hypotheses_dummy.py --iterations 200   # full run
  sudo python3 run_hypotheses_dummy.py --hypothesis H2    # H2 only
"""

import argparse
import subprocess
import time
import random
import os
import sys
import csv
from datetime import datetime

# Add common/ to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
from perf_stat_ruler import (
    measure_pid_perf_stat, measure_cpu_perf_stat, check_perf_stat_available
)
# bpftrace helpers are diagnostic only; reuse the number formatter
from measure_statistically_ebpf import format_number
from perf_record_stats import (stop_and_read, stop_perf_record_graceful,
                               configure_sample_rate_limits)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKLOAD_CPU = "6"
PERF_RECORD_CPU = "7"
RULER_CPU = "14"  # perf stat's userspace process runs here
DURATION = 5  # seconds per measurement window (5s for better accumulation)
PERF_DATA = "/tmp/perf-hypothesis.data"

# perf stat events — 3 hardware + 2 software, no PMU multiplexing
PERF_STAT_EVENTS = [
    "cycles",
    "L1-dcache-load-misses",
    "branch-misses",
    "context-switches",
    "page-faults",
]

# H2: sampling period configs (from most aggressive to least)
# -c 1000 is excluded: pilot runs lost samples every time (perf record could
# not keep up), so it does not measure the requested rate.
H2_CONFIGS = [5000, 10000, 50000, 100000, 500000, 1000000]

# H3: -g ablation configs (label, perf record flags)
H3_CONFIGS = [
    ("with_g",    "-g -c 100000"),
    ("without_g", "-c 100000"),
]

SESSION_ID = datetime.now().strftime("%Y%m%d_%H%M%S")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd, timeout=30):
    try:
        return subprocess.run(
            cmd, shell=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "timeout")


def start_workload(kind):
    """Start the requested workload on the isolated CPU.
    Returns (PID, name), or (None, None). Never substitutes another workload.

    dummy_workload: allocates once, then loops forever (stationary). Main
        workload, so iterations are comparable and independent.
    lat_mem_rd: restarted in a loop and sweeps array sizes, so its behaviour
        varies over time. Secondary workload for the H1 comparison.
    """
    stop_workload()

    if kind == "dummy_workload":
        here = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(here, "dummy_workload.c")
        binary = os.path.join(here, "dummy_workload")
        # Always rebuild so flags are known; frame pointers are for perf -g
        build = run_cmd(f"gcc -O0 -fno-omit-frame-pointer '{src}' -o '{binary}'")
        if build.returncode != 0:
            print(f"  Could not build dummy_workload: {build.stderr.strip()}")
            return None, None
        os.system(f"nohup taskset -c {WORKLOAD_CPU} '{binary}' "
                  f"> /dev/null 2>&1 &")
        time.sleep(3)  # let the one-time array fill finish
        pid = run_cmd("pgrep -x dummy_workload | head -n 1").stdout.strip()
        return (pid, kind) if pid.isdigit() else (None, None)

    lat_mem_cmd = None
    if run_cmd("which lat_mem_rd").returncode == 0:
        lat_mem_cmd = "lat_mem_rd"
    elif os.path.exists("/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd"):
        lat_mem_cmd = "/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd"
    if not lat_mem_cmd:
        return None, None
    # Infinite loop: the workload never terminates during the experiment
    wrapper = f"while true; do {lat_mem_cmd} -t 128M 512; done"
    os.system(
        f"nohup taskset -c {WORKLOAD_CPU} bash -c '{wrapper}' "
        f"> /dev/null 2>&1 &"
    )
    time.sleep(3)
    pid = run_cmd("pgrep -x lat_mem_rd | head -n 1").stdout.strip()
    return (pid, kind) if pid.isdigit() else (None, None)


def stop_workload():
    # Match lat_mem_rd's arguments, not just its name: "-f lat_mem_rd" would
    # also match (and kill) this script run with "--workload lat_mem_rd".
    run_cmd("pkill -9 -f '[l]at_mem_rd -t 128M'")
    run_cmd("pkill -9 -x lat_mem_rd")
    run_cmd("pkill -9 -x dummy_workload")
    time.sleep(0.5)


def start_perf_record(flags="-g -c 100000"):
    """Start perf record profiling CPU 6 (where the workload runs).
    Returns the perf PID or None.
    """
    run_cmd("pkill -9 -x perf 2>/dev/null")
    time.sleep(0.3)
    # -C profiles the whole CPU, not a PID — robust against pid churn.
    # -e cycles (user + kernel, perf's default event) makes the sample rate
    # deterministic: CPU freq / c. cycles:k only sampled lat_mem_rd's rare
    # kernel time, so perf record's cost tracked workload restarts.
    cmd = (
        f"nohup taskset -c {PERF_RECORD_CPU} perf record {flags} "
        f"-e cycles -C {WORKLOAD_CPU} -o {PERF_DATA} "
        f"> /dev/null 2>&1 &"
    )
    os.system(cmd)
    time.sleep(1)  # Let perf record stabilize
    res = run_cmd("pgrep -x perf | head -n 1")
    pid = res.stdout.strip()
    return pid if pid.isdigit() else None


def stop_perf_record():
    stop_perf_record_graceful()


# ---------------------------------------------------------------------------
# CSV Writer
# ---------------------------------------------------------------------------

class ResultsWriter:
    FIELDNAMES = [
        "session_id", "timestamp", "hypothesis", "workload", "config_g",
        "config_c", "iteration", "run_type",
        "cycles", "cache_misses", "branch_misses",
        "page_faults", "context_switches",
        # perf record data quality (tool_overhead rows only; >0 = invalid run)
        "lost_events", "throttle_events",
    ]

    def __init__(self, output_path):
        self.output_path = output_path
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        self.file = open(output_path, 'w', newline='')
        self.writer = csv.DictWriter(self.file, fieldnames=self.FIELDNAMES)
        self.writer.writeheader()

    def write_row(self, hypothesis, workload, config_g, config_c,
                  iteration, run_type, results):
        row = {
            "session_id": SESSION_ID,
            "timestamp": datetime.now().isoformat(),
            "hypothesis": hypothesis,
            "workload": workload,
            "config_g": config_g,
            "config_c": config_c,
            "iteration": iteration,
            "run_type": run_type,
            # Missing metrics are left empty (NaN), never 0, so they
            # cannot bias means. cache_misses = L1-dcache-load-misses.
            "cycles": results.get("cycles", ""),
            "cache_misses": results.get("L1_dcache_misses", ""),
            "branch_misses": results.get("branch_misses", ""),
            "page_faults": results.get("page_faults", ""),
            "context_switches": results.get("context_switches", ""),
            "lost_events": results.get("lost_events", ""),
            "throttle_events": results.get("throttle_events", ""),
        }
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

def measure_workload_baseline(duration):
    """Measure the WORKLOAD CPU's hardware counters via perf stat -C 6.
    No profiler is running during this measurement.
    This gives the denominator for H4 (overhead ratio = tool / workload).

    CPU-wide counting (not PID) because the workload runs lat_mem_rd in a
    while-true loop — each invocation restarts with a different PID.
    Counting an isolated CPU is immune to this PID churn. (Issue 1 fix)
    """
    return measure_cpu_perf_stat(WORKLOAD_CPU, duration,
                                 ruler_cpu=RULER_CPU, events=PERF_STAT_EVENTS)


def measure_tool_overhead(perf_pid, duration):
    """Measure PERF RECORD's own hardware counters via perf stat -p.

    This is the core measurement — the profiling tool's own resource
    consumption.  perf stat's per-thread counters only run while perf
    record is scheduled (CPU 7), so they use CPU 7's PMU; perf record's
    sampling counter is on CPU 6's PMU.  No PMU sharing (Issue 2), no
    ruler NMIs (Issue 3), exact counts (Issue 4).

    Note: this captures perf record's own process (ring buffer reads,
    file writes, plus its syscalls in kernel mode).  The sampling NMI
    handler runs in interrupt context on CPU 6 and is attributed to the
    workload, not to perf record's PID.
    """
    if not perf_pid:
        return None
    return measure_pid_perf_stat(int(perf_pid), duration,
                                 ruler_cpu=RULER_CPU, events=PERF_STAT_EVENTS)


# ---------------------------------------------------------------------------
# H1: Constant Absolute Cost
# ---------------------------------------------------------------------------

def run_h1(writer, iterations, workload_name):
    """Measure perf record's own resource consumption with -g -c 100K.

    Each iteration:
      1. Measure workload baseline (perf stat -C 6, no profiler)
      2. Start perf record -g -c 100K
      3. Measure tool overhead (perf stat on perf record PID)
      4. Stop perf record
    """
    print(f"\n{'='*70}")
    print(f"  H1: Constant Absolute Cost ({iterations} iterations)")
    print(f"  Measuring perf record -g -c 100K own resource consumption")
    print(f"  Method: perf stat on perf record's PID")
    print(f"  Outer ruler: perf stat (CPU {RULER_CPU}), {DURATION}s window")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        print(f"  H1 iter {i+1}/{iterations} (fail: {failures})    ", end='\r')

        # Phase A: Workload baseline (no profiler running)
        baseline = measure_workload_baseline(DURATION)
        if baseline:
            writer.write_row("H1", workload_name, "on", 100000,
                             i+1, "workload_baseline", baseline)

        time.sleep(1)  # brief cooldown

        # Phase B: Start perf record, then measure ITS OWN PID
        perf_pid = start_perf_record("-g -c 100000")
        if not perf_pid:
            failures += 1
            stop_perf_record()
            continue

        tool_data = measure_tool_overhead(perf_pid, DURATION)
        quality = stop_and_read(PERF_DATA)  # lost/throttle flags
        if tool_data:
            tool_data.update(quality)
            if quality['lost_events'] or quality['throttle_events']:
                print(f"\n  ⚠️ iter {i+1}: lost={quality['lost_events']} throttle={quality['throttle_events']} — flagged")

        if tool_data:
            writer.write_row("H1", workload_name, "on", 100000,
                             i+1, "tool_overhead", tool_data)
        else:
            failures += 1

        time.sleep(2)  # cooldown

    print(f"\n  H1 complete. Failures: {failures}/{iterations}")


# ---------------------------------------------------------------------------
# H2: Linear Scaling with Sampling Frequency
# ---------------------------------------------------------------------------

def run_h2(writer, iterations, workload_name):
    """Vary -c flag, randomized block design.

    Each block: one workload baseline + tool overhead for each -c value.
    The baseline is measured once per block (it doesn't depend on -c).
    """
    configs_str = ', '.join(format_number(c) for c in H2_CONFIGS)
    print(f"\n{'='*70}")
    print(f"  H2: Linear Scaling ({iterations} blocks × {len(H2_CONFIGS)} configs)")
    print(f"  Configs: -c [{configs_str}]")
    print(f"  Design: Randomized block (order shuffled per block)")
    print(f"  Measuring perf record's own PID for each config")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        # One workload baseline per block (doesn't depend on -c)
        baseline = measure_workload_baseline(DURATION)
        if baseline:
            writer.write_row("H2", workload_name, "on", 0,
                             i+1, "workload_baseline", baseline)

        time.sleep(1)

        # Randomize config order within each block
        shuffled = H2_CONFIGS.copy()
        random.shuffle(shuffled)

        for c_val in shuffled:
            print(f"  H2 block {i+1}/{iterations}, "
                  f"-c {format_number(c_val)} (fail: {failures})    ", end='\r')

            perf_pid = start_perf_record(f"-g -c {c_val}")
            if not perf_pid:
                failures += 1
                stop_perf_record()
                continue

            tool_data = measure_tool_overhead(perf_pid, DURATION)
            quality = stop_and_read(PERF_DATA)  # lost/throttle flags
            if tool_data:
                tool_data.update(quality)
                if quality['lost_events'] or quality['throttle_events']:
                    print(f"\n  ⚠️ iter {i+1}: lost={quality['lost_events']} throttle={quality['throttle_events']} — flagged")

            if tool_data:
                writer.write_row("H2", workload_name, "on", c_val,
                                 i+1, "tool_overhead", tool_data)
            else:
                failures += 1

            time.sleep(2)  # cooldown between configs

    print(f"\n  H2 complete. Failures: {failures}")


# ---------------------------------------------------------------------------
# H3: Call-Graph Ablation (-g flag)
# ---------------------------------------------------------------------------

def run_h3(writer, iterations, workload_name):
    """Paired design: with-g vs without-g, randomized order per block.

    Each block: one workload baseline + paired tool overhead measurements.
    """
    print(f"\n{'='*70}")
    print(f"  H3: Call-Graph Ablation ({iterations} paired blocks)")
    print(f"  Comparing: -g -c 100K  vs  -c 100K (no -g)")
    print(f"  Design: Paired, order randomized per block")
    print(f"  Measuring perf record's own PID for each config")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        # Workload baseline for this block
        baseline = measure_workload_baseline(DURATION)
        if baseline:
            writer.write_row("H3", workload_name, "baseline", 100000,
                             i+1, "workload_baseline", baseline)

        time.sleep(1)

        configs = H3_CONFIGS.copy()
        random.shuffle(configs)

        for label, flags in configs:
            config_g = "on" if "-g" in flags else "off"
            print(f"  H3 block {i+1}/{iterations}, "
                  f"{label} (fail: {failures})    ", end='\r')

            perf_pid = start_perf_record(flags)
            if not perf_pid:
                failures += 1
                stop_perf_record()
                continue

            tool_data = measure_tool_overhead(perf_pid, DURATION)
            quality = stop_and_read(PERF_DATA)  # lost/throttle flags
            if tool_data:
                tool_data.update(quality)
                if quality['lost_events'] or quality['throttle_events']:
                    print(f"\n  ⚠️ iter {i+1}: lost={quality['lost_events']} throttle={quality['throttle_events']} — flagged")

            if tool_data:
                writer.write_row("H3", workload_name, config_g, 100000,
                                 i+1, "tool_overhead", tool_data)
            else:
                failures += 1

            time.sleep(2)  # cooldown

    print(f"\n  H3 complete. Failures: {failures}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Unified Hypothesis Testing — CPU-Bound Workload (H1, H2, H3)"
    )
    parser.add_argument("--iterations", type=int, default=200,
                        help="Iterations per hypothesis (default: 200)")
    parser.add_argument("--pilot", action="store_true",
                        help="Pilot mode: 50 iterations (quick)")
    parser.add_argument("--hypothesis", type=str,
                        choices=["H1", "H2", "H3", "all"], default="all",
                        help="Which hypothesis to run (default: all)")
    parser.add_argument("--workload", choices=["dummy_workload", "lat_mem_rd"],
                        default="dummy_workload",
                        help="Workload on CPU 6 (default: dummy_workload)")
    parser.add_argument("--output", type=str,
                        default=f"../results/hypotheses_dummy_{SESSION_ID}.csv",
                        help="Output CSV path")
    args = parser.parse_args()

    if args.pilot:
        args.iterations = 50
        print("  🧪 PILOT MODE: 50 iterations per hypothesis\n")

    iters = args.iterations

    configure_sample_rate_limits()

    # Start the workload
    print("  Starting workload...")
    workload_pid, workload_name = start_workload(args.workload)
    if not workload_pid:
        print(f"  FATAL: Could not start {args.workload}.")
        print("  dummy_workload needs gcc; lat_mem_rd needs: sudo apt install -y lmbench")
        sys.exit(1)
    print(f"  Workload: {workload_name} (PID {workload_pid}, CPU {WORKLOAD_CPU})")

    # ---------------------------------------------------------------
    # Sanity check: verify perf stat can measure PERF RECORD's PID
    # This is the critical validation — the entire experiment depends
    # on perf stat being able to count events for perf record's PID.
    # ---------------------------------------------------------------
    if not check_perf_stat_available(ruler_cpu=RULER_CPU):
        stop_workload()
        return

    print(f"\n  Sanity check: starting perf record and measuring its PID...")
    test_perf_pid = start_perf_record("-g -c 100000")
    if not test_perf_pid:
        print("  ❌ Could not start perf record. Check permissions (run with sudo).")
        stop_workload()
        return

    print(f"  perf record PID: {test_perf_pid}")
    print(f"  Running perf stat for {DURATION}s against perf record PID {test_perf_pid}...")
    test_result = measure_pid_perf_stat(int(test_perf_pid), DURATION,
                                        ruler_cpu=RULER_CPU,
                                        events=PERF_STAT_EVENTS, verbose=True)
    stop_perf_record()
    if test_result:
        print(f"  ✅ Sanity check PASSED — got {len(test_result)} metrics from perf record")
        for k, v in test_result.items():
            print(f"     {k}: {format_number(v)}")
    else:
        print("  ❌ Sanity check FAILED — perf stat returned no data for perf record's PID.")
        print("  Possible causes:")
        print("    - perf_event_paranoid > -1 (run: sudo sysctl kernel.perf_event_paranoid=-1)")
        print("    - counters multiplexed (NMI watchdog: sudo sysctl kernel.nmi_watchdog=0)")
        print(f"    - CPU {RULER_CPU} not isolated (check: grep isolcpus /proc/cmdline)")
        print("    - perf record exited too fast (try: pgrep -x perf)")
        print("  Aborting.")
        stop_workload()
        return

    # Also verify we can measure the workload CPU
    print(f"\n  Verifying workload baseline measurement (perf stat -C {WORKLOAD_CPU})...")
    wl_result = measure_workload_baseline(DURATION)
    if wl_result:
        print(f"  ✅ Workload baseline OK — got {len(wl_result)} metrics")
        for k, v in wl_result.items():
            print(f"     {k}: {format_number(v)}")
    else:
        print("  ⚠️ Could not measure workload baseline. H4 ratio data will be missing.")

    # ---------------------------------------------------------------
    # Time estimation
    # ---------------------------------------------------------------
    # Each perf stat call: DURATION + ~1s startup/teardown (no compilation)
    stat_time = DURATION + 1
    time_map = {
        # H1: per iter: 1 baseline + 1 tool + cooldowns
        "H1": iters * (stat_time + 1 + 1 + stat_time + 2),
        # H2: per block: 1 baseline + 6 configs × (start + tool + cooldown)
        "H2": iters * (stat_time + 1 + len(H2_CONFIGS) * (1 + stat_time + 2)),
        # H3: per block: 1 baseline + 2 configs × (start + tool + cooldown)
        "H3": iters * (stat_time + 1 + len(H3_CONFIGS) * (1 + stat_time + 2)),
    }
    if args.hypothesis == "all":
        total_sec = sum(time_map.values())
    else:
        total_sec = time_map[args.hypothesis]
    est_min = total_sec / 60

    print(f"\n{'='*70}")
    print(f"  Unified Hypothesis Testing — {workload_name}")
    print(f"  Session:      {SESSION_ID}")
    print(f"  Hypothesis:   {args.hypothesis}")
    print(f"  Iterations:   {iters} per hypothesis")
    print(f"  Duration:     {DURATION}s per measurement window")
    print(f"  Est. time:    ~{est_min:.0f} minutes")
    print(f"  Method:       perf stat on perf record's OWN PID (CPU {RULER_CPU})")
    print(f"  Inner tool:   perf record on CPU {PERF_RECORD_CPU}")
    print(f"  Output:       {args.output}")
    print(f"{'='*70}")

    writer = ResultsWriter(args.output)

    try:
        if args.hypothesis in ("all", "H1"):
            run_h1(writer, iters, workload_name)

        if args.hypothesis in ("all", "H2"):
            run_h2(writer, iters, workload_name)

        if args.hypothesis in ("all", "H3"):
            run_h3(writer, iters, workload_name)

    except KeyboardInterrupt:
        print("\n\n  ⚠️ Interrupted! Saving partial results...")
    finally:
        writer.close()
        stop_workload()
        stop_perf_record()

    print(f"\n{'='*70}")
    print(f"  ✅ COMPLETE — Results: {args.output}")
    print(f"  Next: python3 ../common/analyze_hypotheses.py {args.output}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
