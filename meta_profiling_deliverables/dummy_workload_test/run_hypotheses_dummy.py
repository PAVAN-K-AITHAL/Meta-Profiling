#!/usr/bin/env python3
"""
run_hypotheses_dummy.py — Unified Hypothesis Testing for CPU-Bound Workload

Runs H1, H2, and H3 on lat_mem_rd (lmbench) using bpftrace/eBPF as the
outer measurement ruler — consistent with our existing measurement methodology.

H1: Constant absolute cost — measures perf record's overhead at -g -c 100K.
    Data later compared with Go API results in analyze_hypotheses.py.
H2: Linear scaling — varies -c across 6 sampling periods (randomized blocks).
H3: -g ablation — paired with-g vs without-g (randomized order per block).

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
from measure_statistically_ebpf import run_bpftrace, METRICS, format_number

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
    """Start lat_mem_rd on the isolated CPU. Returns PID."""
    run_cmd("pkill -9 lat_mem_rd 2>/dev/null")
    run_cmd("pkill -9 dummy_workload 2>/dev/null")
    time.sleep(0.3)

    # Try lat_mem_rd first (lmbench)
    if run_cmd("which lat_mem_rd").returncode == 0:
        # lat_mem_rd -t <array_size_MB> <stride_bytes>
        # 128M array defeats prefetchers, 512-byte stride
        run_cmd(f"taskset -c {WORKLOAD_CPU} lat_mem_rd -t 128M 512 > /dev/null 2>&1 &")
        time.sleep(2)
        res = run_cmd("pgrep -x lat_mem_rd | head -n 1")
        pid = res.stdout.strip()
        if pid.isdigit():
            return pid, "lat_mem_rd"

    # Fallback to dummy_workload
    if os.path.exists("./dummy_workload") or os.path.exists("dummy_workload.c"):
        if not os.path.exists("./dummy_workload"):
            run_cmd("gcc dummy_workload.c -o dummy_workload")
        run_cmd(f"taskset -c {WORKLOAD_CPU} ./dummy_workload > /dev/null &")
        time.sleep(1)
        res = run_cmd("pgrep -x dummy_workload | head -n 1")
        pid = res.stdout.strip()
        if pid.isdigit():
            return pid, "dummy_workload"

    return None, None


def stop_workload(workload_name):
    run_cmd(f"pkill -9 {workload_name} 2>/dev/null")


def start_perf_record(target_pid, flags="-g -c 100000"):
    run_cmd("pkill -9 -x perf 2>/dev/null")
    time.sleep(0.2)
    cmd = (
        f"taskset -c {PERF_RECORD_CPU} perf record {flags} "
        f"-e cycles:k -p {target_pid} -o /tmp/perf-hypothesis.data "
        f">/dev/null 2>&1 &"
    )
    run_cmd(cmd)
    time.sleep(0.5)
    res = run_cmd("pgrep -x perf | head -n 1")
    pid = res.stdout.strip()
    return pid if pid.isdigit() else None


def stop_perf_record():
    run_cmd("pkill -9 -x perf 2>/dev/null")
    time.sleep(0.2)


# ---------------------------------------------------------------------------
# CSV Writer
# ---------------------------------------------------------------------------

class ResultsWriter:
    FIELDNAMES = [
        "session_id", "timestamp", "hypothesis", "workload", "config_g",
        "config_c", "iteration", "run_type",
        "cycles", "cache_misses", "l1d_misses", "branch_misses",
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
            "l1d_misses": results.get("L1-dcache-load-misses", 0),
            "branch_misses": results.get("branch-misses", 0),
            "page_faults": results.get("page-faults", 0),
            "context_switches": results.get("context-switches", 0),
        }
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()


# ---------------------------------------------------------------------------
# H1: Constant Absolute Cost
# ---------------------------------------------------------------------------

def run_h1(workload_pid, writer, iterations, workload_name):
    """Measure perf record -g -c 100K overhead using eBPF."""
    print(f"\n{'='*70}")
    print(f"  H1: Constant Absolute Cost ({iterations} iterations)")
    print(f"  Measuring perf record -g -c 100K overhead on {workload_name}")
    print(f"  Outer ruler: bpftrace (eBPF) on CPU {BPFTRACE_CPU}")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        print(f"  H1 iter {i+1}/{iterations} (fail: {failures})    ", end='\r')

        # Start perf record on the workload
        perf_pid = start_perf_record(workload_pid, "-g -c 100000")
        if not perf_pid:
            failures += 1
            stop_perf_record()
            continue

        # Measure perf record's overhead via bpftrace
        results = run_bpftrace(perf_pid, DURATION)
        if results:
            writer.write_row("H1", workload_name, "on", 100000,
                             i+1, "tool_overhead", results)
        else:
            failures += 1

        stop_perf_record()
        time.sleep(1)

    print(f"\n  H1 complete. Failures: {failures}/{iterations}")


# ---------------------------------------------------------------------------
# H2: Linear Scaling with Sampling Frequency
# ---------------------------------------------------------------------------

def run_h2(workload_pid, writer, iterations, workload_name):
    """Vary -c flag, randomized block design."""
    configs_str = ', '.join(format_number(c) for c in H2_CONFIGS)
    print(f"\n{'='*70}")
    print(f"  H2: Linear Scaling ({iterations} blocks × {len(H2_CONFIGS)} configs)")
    print(f"  Configs: -c [{configs_str}]")
    print(f"  Design: Randomized block (order shuffled per block)")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        # Randomize config order within each block
        shuffled = H2_CONFIGS.copy()
        random.shuffle(shuffled)

        for c_val in shuffled:
            print(f"  H2 block {i+1}/{iterations}, "
                  f"-c {format_number(c_val)} (fail: {failures})    ", end='\r')

            perf_pid = start_perf_record(workload_pid, f"-g -c {c_val}")
            if not perf_pid:
                failures += 1
                stop_perf_record()
                continue

            results = run_bpftrace(perf_pid, DURATION)
            if results:
                writer.write_row("H2", workload_name, "on", c_val,
                                 i+1, "tool_overhead", results)
            else:
                failures += 1

            stop_perf_record()
            time.sleep(2)  # cooldown between configs

    print(f"\n  H2 complete. Failures: {failures}")


# ---------------------------------------------------------------------------
# H3: Call-Graph Ablation (-g flag)
# ---------------------------------------------------------------------------

def run_h3(workload_pid, writer, iterations, workload_name):
    """Paired design: with-g vs without-g, randomized order per block."""
    print(f"\n{'='*70}")
    print(f"  H3: Call-Graph Ablation ({iterations} paired blocks)")
    print(f"  Comparing: -g -c 100K  vs  -c 100K (no -g)")
    print(f"  Design: Paired, order randomized per block")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        configs = H3_CONFIGS.copy()
        random.shuffle(configs)

        for label, flags in configs:
            config_g = "on" if "-g" in flags else "off"
            print(f"  H3 block {i+1}/{iterations}, "
                  f"{label} (fail: {failures})    ", end='\r')

            perf_pid = start_perf_record(workload_pid, flags)
            if not perf_pid:
                failures += 1
                stop_perf_record()
                continue

            results = run_bpftrace(perf_pid, DURATION)
            if results:
                writer.write_row("H3", workload_name, config_g, 100000,
                                 i+1, "tool_overhead", results)
            else:
                failures += 1

            stop_perf_record()
            time.sleep(2)

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

    # Estimate time
    runs_map = {
        "H1": iters,
        "H2": iters * len(H2_CONFIGS),
        "H3": iters * len(H3_CONFIGS),
    }
    if args.hypothesis == "all":
        total_runs = sum(runs_map.values())
    else:
        total_runs = runs_map[args.hypothesis]
    # Each run ≈ DURATION + bpftrace compile time (~10s first, ~3s after) + cooldown
    est_min = total_runs * (DURATION + 4) / 60

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
            run_h1(workload_pid, writer, iters, workload_name)

        if args.hypothesis in ("all", "H2"):
            run_h2(workload_pid, writer, iters, workload_name)

        if args.hypothesis in ("all", "H3"):
            run_h3(workload_pid, writer, iters, workload_name)

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
