#!/usr/bin/env python3
"""
run_hypotheses_dummy.py — Unified Hypothesis Testing for CPU-Bound Workload

Measurement methodology (per mentor guidance):
  eBPF (bpftrace) measures perf record's OWN PID to count the exact
  hardware events (cycles, cache-misses, branch-misses) consumed by
  the profiling tool itself.  This is the "instrumentation of
  instrumentation" approach — a non-sampling outer ruler counting
  the inner tool's overhead via kernel probes.

Per-iteration flow:
  1. [Workload baseline] bpftrace → workload PID (no profiler)
  2. Start perf record (inner tool under study) on CPU 7
  3. [Tool overhead] bpftrace → perf record's PID for DURATION seconds
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
  Workload (lat_mem_rd):  CPU 6  (Core 6, Socket 0)
  perf record:            CPU 7  (Core 7, Socket 0)
  bpftrace:               unpinned (BPF probes fire in kernel on target CPU)

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
from measure_statistically_ebpf import run_bpftrace, run_bpftrace_cpu, run_bpftrace_pid_cpu, METRICS, format_number

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKLOAD_CPU = "6"
PERF_RECORD_CPU = "7"
DURATION = 5  # seconds per measurement window (5s for better accumulation)

# H2: sampling period configs (from most aggressive to least)
H2_CONFIGS = [1000, 10000, 50000, 100000, 500000, 1000000]

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


def start_workload():
    """Start lat_mem_rd on the isolated CPU. Returns (PID, name).

    lat_mem_rd is wrapped in an infinite loop so it never terminates
    during the experiment.  We use 'nohup bash -c ...' so the shell
    doesn't capture the subprocess inside run_cmd's pipe.
    """
    run_cmd("pkill -9 -f lat_mem_rd 2>/dev/null")
    run_cmd("pkill -9 -f dummy_workload 2>/dev/null")
    time.sleep(0.5)

    # Find lat_mem_rd binary
    lat_mem_cmd = None
    if run_cmd("which lat_mem_rd").returncode == 0:
        lat_mem_cmd = "lat_mem_rd"
    elif os.path.exists("/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd"):
        lat_mem_cmd = "/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd"

    if lat_mem_cmd:
        # Infinite loop — workload never terminates during experiment
        wrapper = f"while true; do {lat_mem_cmd} -t 128M 512; done"
        os.system(
            f"nohup taskset -c {WORKLOAD_CPU} bash -c '{wrapper}' "
            f"> /dev/null 2>&1 &"
        )
        time.sleep(3)
        # Find the lat_mem_rd child process (the actual workload)
        res = run_cmd("pgrep -x lat_mem_rd | head -n 1")
        pid = res.stdout.strip()
        if pid.isdigit():
            return pid, "lat_mem_rd"
        # Try the bash wrapper itself
        res = run_cmd("pgrep -f 'lat_mem_rd -t 128M' | head -n 1")
        pid = res.stdout.strip()
        if pid.isdigit():
            return pid, "lat_mem_rd"

    # Fallback to dummy_workload
    if os.path.exists("./dummy_workload") or os.path.exists("dummy_workload.c"):
        if not os.path.exists("./dummy_workload"):
            run_cmd("gcc dummy_workload.c -o dummy_workload")
        os.system(f"taskset -c {WORKLOAD_CPU} ./dummy_workload > /dev/null 2>&1 &")
        time.sleep(1)
        res = run_cmd("pgrep -x dummy_workload | head -n 1")
        pid = res.stdout.strip()
        if pid.isdigit():
            return pid, "dummy_workload"

    return None, None


def stop_workload(workload_name):
    run_cmd("pkill -9 -f 'lat_mem_rd' 2>/dev/null")
    run_cmd("pkill -9 -f 'dummy_workload' 2>/dev/null")
    time.sleep(0.3)


def start_perf_record(flags="-g -c 100000"):
    """Start perf record profiling CPU 6 (where the workload runs).
    Returns the perf PID or None.
    """
    run_cmd("pkill -9 -x perf 2>/dev/null")
    time.sleep(0.3)
    # -C profiles the whole CPU, not a PID — robust against pid churn
    cmd = (
        f"nohup taskset -c {PERF_RECORD_CPU} perf record {flags} "
        f"-e cycles:k -C {WORKLOAD_CPU} -o /tmp/perf-hypothesis.data "
        f"> /dev/null 2>&1 &"
    )
    os.system(cmd)
    time.sleep(1)  # Let perf record stabilize
    res = run_cmd("pgrep -x perf | head -n 1")
    pid = res.stdout.strip()
    return pid if pid.isdigit() else None


def stop_perf_record():
    run_cmd("pkill -INT -x perf 2>/dev/null")  # graceful stop first
    time.sleep(0.3)
    run_cmd("pkill -9 -x perf 2>/dev/null")    # force kill
    time.sleep(0.2)


def refresh_workload_pid():
    """Re-find the lat_mem_rd PID (it changes on each loop iteration)."""
    res = run_cmd("pgrep -x lat_mem_rd | head -n 1")
    pid = res.stdout.strip()
    if pid.isdigit():
        return pid
    res = run_cmd("pgrep -f 'lat_mem_rd -t 128M' | head -n 1")
    pid = res.stdout.strip()
    return pid if pid.isdigit() else None


# ---------------------------------------------------------------------------
# CSV Writer
# ---------------------------------------------------------------------------

class ResultsWriter:
    FIELDNAMES = [
        "session_id", "timestamp", "hypothesis", "workload", "config_g",
        "config_c", "iteration", "run_type",
        "cycles", "cache_misses", "branch_misses",
        "page_faults", "context_switches"
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
            "cycles": results.get("cycles", 0),
            "cache_misses": results.get("cache-misses", 0),
            "branch_misses": results.get("branch-misses", 0),
            "page_faults": results.get("page-faults", 0),
            "context_switches": results.get("context-switches", 0),
        }
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

def measure_workload_baseline(duration):
    """Measure the WORKLOAD's own hardware counters via bpftrace.
    No profiler is running during this measurement.
    This gives the denominator for H4 (overhead ratio = tool / workload).

    Uses CPU-based filtering (not PID) because the workload runs
    lat_mem_rd in a while-true loop — each invocation exits and restarts
    with a different PID. CPU filtering on an isolated CPU is immune
    to this PID churn. (Issue 1 fix)
    """
    return run_bpftrace_cpu(WORKLOAD_CPU, duration)


def measure_tool_overhead(perf_pid, duration):
    """Measure PERF RECORD's own hardware counters via bpftrace.

    This is the core measurement — the profiling tool's own resource
    consumption (cycles, cache-misses, branch-misses).  bpftrace counts
    hardware events attributed to perf record's PID on its pinned CPU.

    Uses dual PID+CPU filter (ebpf_counter_pid_cpu.bt) to:
      - Eliminate PMU contention on CPU 6 where perf record samples (Issue 2)
      - Reduce NMI observer effect by limiting probe scope (Issue 3)

    Note: this captures perf record's USERSPACE processing overhead
    (ring buffer reads, file writes, symbol resolution).  The NMI
    handler overhead runs in interrupt context under the workload's
    PID and is not captured by PID-filtered probes.
    """
    if not perf_pid:
        return None
    return run_bpftrace_pid_cpu(perf_pid, PERF_RECORD_CPU, duration)


# ---------------------------------------------------------------------------
# H1: Constant Absolute Cost
# ---------------------------------------------------------------------------

def run_h1(writer, iterations, workload_name):
    """Measure perf record's own resource consumption with -g -c 100K.

    Each iteration:
      1. Measure workload baseline (bpftrace on workload PID, no profiler)
      2. Start perf record -g -c 100K
      3. Measure tool overhead (bpftrace on perf record PID)
      4. Stop perf record
    """
    print(f"\n{'='*70}")
    print(f"  H1: Constant Absolute Cost ({iterations} iterations)")
    print(f"  Measuring perf record -g -c 100K own resource consumption")
    print(f"  Method: bpftrace on perf record's PID")
    print(f"  Outer ruler: bpftrace (eBPF), {DURATION}s window")
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
        stop_perf_record()

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
            stop_perf_record()

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
            stop_perf_record()

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
    parser.add_argument("--output", type=str,
                        default=f"../results/hypotheses_dummy_{SESSION_ID}.csv",
                        help="Output CSV path")
    args = parser.parse_args()

    if args.pilot:
        args.iterations = 50
        print("  🧪 PILOT MODE: 50 iterations per hypothesis\n")

    iters = args.iterations

    # Start the workload
    print("  Starting workload...")
    workload_pid, workload_name = start_workload()
    if not workload_pid:
        print("  FATAL: Could not start lat_mem_rd or dummy_workload.")
        print("  Install lmbench: sudo apt install -y lmbench")
        return
    print(f"  Workload: {workload_name} (PID {workload_pid}, CPU {WORKLOAD_CPU})")

    # ---------------------------------------------------------------
    # Sanity check: verify bpftrace can measure PERF RECORD's PID
    # This is the critical validation — the entire experiment depends
    # on bpftrace being able to count events for perf record's PID.
    # ---------------------------------------------------------------
    print(f"\n  Sanity check: starting perf record and measuring its PID...")
    test_perf_pid = start_perf_record("-g -c 100000")
    if not test_perf_pid:
        print("  ❌ Could not start perf record. Check permissions (run with sudo).")
        stop_workload(workload_name)
        return

    print(f"  perf record PID: {test_perf_pid}")
    print(f"  Running bpftrace for {DURATION}s against perf record PID+CPU {PERF_RECORD_CPU}...")
    test_result = run_bpftrace_pid_cpu(test_perf_pid, PERF_RECORD_CPU, DURATION)
    stop_perf_record()
    if test_result:
        print(f"  ✅ Sanity check PASSED — got {len(test_result)} metrics from perf record")
        for k, v in test_result.items():
            print(f"     {k}: {format_number(v)}")
    else:
        print("  ❌ Sanity check FAILED — bpftrace returned no data for perf record's PID.")
        print("  Possible causes:")
        print("    - bpftrace cannot attach hardware probes (check: sudo bpftrace -l 'hardware:*')")
        print("    - Overflow periods still too high (check ebpf_counter.bt)")
        print("    - perf record PID mismatch (try: pgrep -x perf)")
        print("  Aborting.")
        stop_workload(workload_name)
        return

    # Also verify we can measure the workload via CPU-based filtering
    print(f"\n  Verifying workload baseline measurement (CPU {WORKLOAD_CPU} filter)...")
    wl_result = run_bpftrace_cpu(WORKLOAD_CPU, DURATION)
    if wl_result:
        print(f"  ✅ Workload baseline OK — got {len(wl_result)} metrics")
        for k, v in wl_result.items():
            print(f"     {k}: {format_number(v)}")
    else:
        print("  ⚠️ Could not measure workload baseline via CPU filter. H4 ratio data will be missing.")

    # ---------------------------------------------------------------
    # Time estimation
    # ---------------------------------------------------------------
    # Each bpftrace call: ~DURATION + 10s compilation overhead
    bpf_time = DURATION + 12  # conservative estimate per bpftrace call
    time_map = {
        # H1: per iter: 1 baseline + 1 tool + cooldowns
        "H1": iters * (bpf_time + 1 + 1 + bpf_time + 2),
        # H2: per block: 1 baseline + 6 configs × (start + tool + cooldown)
        "H2": iters * (bpf_time + 1 + len(H2_CONFIGS) * (1 + bpf_time + 2)),
        # H3: per block: 1 baseline + 2 configs × (start + tool + cooldown)
        "H3": iters * (bpf_time + 1 + len(H3_CONFIGS) * (1 + bpf_time + 2)),
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
    print(f"  Method:       bpftrace on perf record's OWN PID")
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
        stop_workload(workload_name)
        stop_perf_record()

    print(f"\n{'='*70}")
    print(f"  ✅ COMPLETE — Results: {args.output}")
    print(f"  Next: python3 ../common/analyze_hypotheses.py {args.output}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
