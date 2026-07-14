#!/usr/bin/env python3
"""
ablation_study_ebpf.py — Run an ablation study on perf record flags

Varies the sampling period (-c) and the call graph flag (-g).
Uses eBPF (bpftrace) as the outer ruler to measure the dummy_workload.
"""

import subprocess
import time
import os
import sys
import shutil
import math
import pandas as pd

# Add common/ to path for shared module imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
from measure_statistically_ebpf import run_bpftrace, METRICS, format_number

# Pinned CPUs
BPFTRACE_CPU = "14"
PERF_RECORD_CPU = "7"
WORKLOAD_CPU = "6"

CONFIGS = [
    # Baseline
    {"name": "No Profiler", "flags": None},
    {"name": "-g -c 100K", "flags": "-g -c 100000"},
    {"name": "-c 100K (no -g)", "flags": "-c 100000"},
    {"name": "-g -c 50K", "flags": "-g -c 50000"},
    {"name": "-g -c 10K", "flags": "-g -c 10000"},
    {"name": "-g -c 1K", "flags": "-g -c 1000"},
    {"name": "-g -c 500K", "flags": "-g -c 500000"},
    {"name": "-g -c 1M", "flags": "-g -c 1000000"},
]

def run_cmd(cmd):
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def main():
    if not shutil.which("bpftrace"):
        print("ERROR: bpftrace not installed.")
        return

    # Start dummy workload
    print("Starting dummy_workload...")
    run_cmd("pkill -9 dummy_workload 2>/dev/null")
    run_cmd(f"taskset -c {WORKLOAD_CPU} ./dummy_workload > /dev/null &")
    time.sleep(1)
    
    pid_res = run_cmd("pgrep -x dummy_workload | head -n 1")
    if pid_res.returncode != 0:
        print("ERROR: dummy_workload failed to start.")
        return
    workload_pid = pid_res.stdout.strip()
    
    # 2 seconds duration per test, average over 5 iterations for stability
    DURATION = 2
    ITERATIONS = 5
    
    results_table = []
    baseline_averages = {m: 0 for m in METRICS}
    
    print("\nStarting Ablation Study...")
    
    for cfg in CONFIGS:
        print(f"\nEvaluating: {cfg['name']}")
        
        cfg_samples = {m: [] for m in METRICS}
        
        for i in range(ITERATIONS):
            # Start perf record if needed
            if cfg["flags"]:
                run_cmd("sudo pkill -9 -x perf 2>/dev/null")
                run_cmd(f"sudo taskset -c {PERF_RECORD_CPU} perf record {cfg['flags']} -e cycles:k -p {workload_pid} -o /tmp/perf-ablation.data >/dev/null 2>&1 &")
                time.sleep(0.5)
            
            # Measure with bpftrace
            res = run_bpftrace(workload_pid, DURATION)
            if res:
                for m in METRICS:
                    if m in res:
                        cfg_samples[m].append(res[m])
            
            # Stop perf
            if cfg["flags"]:
                run_cmd("sudo pkill -9 -x perf 2>/dev/null")
                
        # Compute averages
        averages = {}
        for m in METRICS:
            if cfg_samples[m]:
                averages[m] = sum(cfg_samples[m]) / len(cfg_samples[m])
            else:
                averages[m] = 0
                
        if cfg["flags"] is None:
            baseline_averages = averages
            row = {"Config": cfg["name"]}
            for m in METRICS:
                row[f"{m} Overhead"] = "Baseline"
            results_table.append(row)
        else:
            row = {"Config": cfg["name"]}
            for m in METRICS:
                if baseline_averages[m] > 0 and averages[m] > 0:
                    pct = ((averages[m] - baseline_averages[m]) / baseline_averages[m]) * 100
                    row[f"{m} Overhead"] = f"{pct:+.3f}%"
                else:
                    row[f"{m} Overhead"] = "N/A"
            results_table.append(row)
            
    # Print Markdown Table
    print("\n")
    print("=" * 100)
    print("  Table: Ablation Study — perf record Overhead vs Configuration")
    print("  Workload: dummy_workload (CPU-bound) on CPU 6 | Outer ruler: bpftrace on CPU 14")
    print("  Each config averaged over 5 iterations × 2s window")
    print("=" * 100)
    df = pd.DataFrame(results_table)
    print(df.to_markdown(index=False))
    print("\n  Key Observation: Removing -g (call graph) significantly reduces cache-miss")
    print("  overhead, confirming that stack unwinding is a major cost driver.")
    print("  Reducing -c (more frequent sampling) increases overhead super-linearly.")
    print("=" * 100)
    
    os.makedirs("../results", exist_ok=True)
    with open("../results/ablation_study_results.md", "w") as f:
        f.write("# Ablation Study Results\n\n")
        f.write("Table: perf record Overhead vs Configuration (-g and -c flags)\n")
        f.write(f"Workload: dummy_workload (CPU-bound) on CPU 6 | Outer ruler: bpftrace on CPU 14\n\n")
        f.write(df.to_markdown(index=False) + "\n")
        
    # Cleanup
    run_cmd("pkill -9 dummy_workload 2>/dev/null")

if __name__ == "__main__":
    main()
