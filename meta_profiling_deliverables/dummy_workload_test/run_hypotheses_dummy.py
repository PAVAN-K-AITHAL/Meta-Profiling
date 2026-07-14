#!/usr/bin/env python3
"""
run_hypotheses_dummy.py — Unified Hypothesis Testing for CPU-Bound Workload

Measurement methodology:
  - bpftrace measures the WORKLOAD's hardware counters (CPU 6)
  - Phase A (baseline): measure workload counters with NO profiler
  - Phase B (profiled): measure workload counters WITH perf record running
  - Overhead = Phase B - Phase A  (the extra events caused by perf record)

This is the correct approach because perf record is an event-driven tool
that sleeps most of the time — measuring perf record's own PID yields
near-zero counters. Instead, we measure how perf record PERTURBS the
workload's execution.

H1: Constant absolute cost — does the perturbation stay the same
    regardless of the workload? (Compare dummy vs Go API in analyzer.)
H2: Linear scaling — does perturbation scale linearly with 1/c?
H3: -g ablation — does removing -g reduce perturbation?

CPU pinning (Intel Xeon E5-2620 v4, isolated: 6,7,14,15,22,23,30,31):
  lat_mem_rd:       CPU 6  (Core 6, Socket 0)
  perf record:      CPU 7  (Core 7, Socket 0)
  bpftrace ruler:   CPU 14 (Core 6, Socket 1)

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
from measure_statistically_ebpf import run_bpftrace_cpu, METRICS, format_number

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKLOAD_CPU = "6"
PERF_RECORD_CPU = "7"
BPFTRACE_CPU = "14"
DURATION = 2  # seconds per measurement window

# H2: sampling period configs
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
        # Use nohup + bash -c so it survives independent of this shell.
        # Redirect to /dev/null to avoid blocking on I/O.
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
    time.sleep(1)
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

def measure_workload(duration):
    """Measure the WORKLOAD's hardware counters via bpftrace.
    
    We measure the workload CPU (CPU 6), not perf record's PID.
    This is the correct approach — perf record perturbs the workload,
    so we measure that perturbation by comparing workload counters
    with and without perf record running.
    """
    return run_bpftrace_cpu(WORKLOAD_CPU, duration)


# ---------------------------------------------------------------------------
# H1: Constant Absolute Cost
# ---------------------------------------------------------------------------

def run_h1(writer, iterations, workload_name):
    """Measure perf record -g -c 100K overhead using eBPF.
    
    Each iteration:
      1. Measure workload counters WITHOUT perf record (baseline)
      2. Measure workload counters WITH perf record (profiled)
    The difference is the overhead caused by perf record.
    """
    print(f"\n{'='*70}")
    print(f"  H1: Constant Absolute Cost ({iterations} iterations)")
    print(f"  Measuring perf record -g -c 100K overhead on {workload_name}")
    print(f"  Method: baseline vs profiled workload counters on CPU {WORKLOAD_CPU}")
    print(f"  Outer ruler: bpftrace (eBPF) on CPU {BPFTRACE_CPU}")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        print(f"  H1 iter {i+1}/{iterations} (fail: {failures})    ", end='\r')

        # Phase A: Baseline — measure workload with NO profiler
        baseline = measure_workload(DURATION)
        if not baseline:
            failures += 1
            continue

        writer.write_row("H1", workload_name, "on", 100000,
                         i+1, "baseline", baseline)

        # Phase B: Profiled — measure workload WITH perf record running
        perf_pid = start_perf_record("-g -c 100000")
        if not perf_pid:
            failures += 1
            stop_perf_record()
            continue

        profiled = measure_workload(DURATION)
        stop_perf_record()

        if profiled:
            writer.write_row("H1", workload_name, "on", 100000,
                             i+1, "profiled", profiled)
        else:
            failures += 1

        time.sleep(1)  # cooldown

    print(f"\n  H1 complete. Failures: {failures}/{iterations}")


# ---------------------------------------------------------------------------
# H2: Linear Scaling with Sampling Frequency
# ---------------------------------------------------------------------------

def run_h2(writer, iterations, workload_name):
    """Vary -c flag, randomized block design.
    
    Each block: one baseline + measurements for each -c value.
    """
    configs_str = ', '.join(format_number(c) for c in H2_CONFIGS)
    print(f"\n{'='*70}")
    print(f"  H2: Linear Scaling ({iterations} blocks × {len(H2_CONFIGS)} configs)")
    print(f"  Configs: -c [{configs_str}]")
    print(f"  Design: Randomized block (order shuffled per block)")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        # One baseline per block
        baseline = measure_workload(DURATION)
        if baseline:
            writer.write_row("H2", workload_name, "on", 0,
                             i+1, "baseline", baseline)

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

            results = measure_workload(DURATION)
            stop_perf_record()

            if results:
                writer.write_row("H2", workload_name, "on", c_val,
                                 i+1, "profiled", results)
            else:
                failures += 1

            time.sleep(1)  # cooldown between configs

    print(f"\n  H2 complete. Failures: {failures}")


# ---------------------------------------------------------------------------
# H3: Call-Graph Ablation (-g flag)
# ---------------------------------------------------------------------------

def run_h3(writer, iterations, workload_name):
    """Paired design: with-g vs without-g, randomized order per block."""
    print(f"\n{'='*70}")
    print(f"  H3: Call-Graph Ablation ({iterations} paired blocks)")
    print(f"  Comparing: -g -c 100K  vs  -c 100K (no -g)")
    print(f"  Design: Paired, order randomized per block")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        # Baseline for this block
        baseline = measure_workload(DURATION)
        if baseline:
            writer.write_row("H3", workload_name, "baseline", 100000,
                             i+1, "baseline", baseline)

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

            results = measure_workload(DURATION)
            stop_perf_record()

            if results:
                writer.write_row("H3", workload_name, config_g, 100000,
                                 i+1, "profiled", results)
            else:
                failures += 1

            time.sleep(1)

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

    # Sanity check: verify bpftrace can measure this CPU
    print(f"\n  Sanity check: running bpftrace for 2s against CPU {WORKLOAD_CPU}...")
    test_result = run_bpftrace_cpu(WORKLOAD_CPU, 2)
    
    if test_result:
        print(f"  ✅ Sanity check PASSED — got {len(test_result)} metrics")
        for k, v in test_result.items():
            print(f"     {k}: {format_number(v)}")
    else:
        print("  ❌ Sanity check FAILED — bpftrace returned no data.")
        print("  Possible causes:")
        print("    - bpftrace cannot attach hardware probes (check: sudo bpftrace -l 'hardware:*')")
        print("  Aborting.")
        stop_workload(workload_name)
        return

    # Estimate time
    runs_map = {
        "H1": iters * 2,   # baseline + profiled per iter
        "H2": iters * (1 + len(H2_CONFIGS)),  # 1 baseline + N configs
        "H3": iters * (1 + len(H3_CONFIGS)),  # 1 baseline + 2 configs
    }
    if args.hypothesis == "all":
        total_runs = sum(runs_map.values())
    else:
        total_runs = runs_map[args.hypothesis]
    est_min = total_runs * (DURATION + 5) / 60

    print(f"\n{'='*70}")
    print(f"  Unified Hypothesis Testing — {workload_name}")
    print(f"  Session:      {SESSION_ID}")
    print(f"  Hypothesis:   {args.hypothesis}")
    print(f"  Iterations:   {iters} per hypothesis")
    print(f"  Duration:     {DURATION}s per window")
    print(f"  Total runs:   {total_runs}")
    print(f"  Est. time:    ~{est_min:.0f} minutes")
    print(f"  Outer ruler:  bpftrace (eBPF) on CPU {BPFTRACE_CPU}")
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
