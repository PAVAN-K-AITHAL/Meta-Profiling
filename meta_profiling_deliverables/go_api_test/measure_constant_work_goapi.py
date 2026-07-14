#!/usr/bin/env python3
"""
measure_constant_work_goapi.py — Constant Work Absolute Overhead Measurement

Instead of measuring for a fixed duration, this script measures exactly 100,000 
requests to the Go API using k6. It computes the absolute per-request CPU cycle overhead 
added by the inner profiling tool.
"""

import subprocess
import time
import os
import signal
import re
import pandas as pd

BPFTRACE_CPU = "14"
PERF_RECORD_CPU = "7"
EBPF_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common", "ebpf_counter_infinite.bt")
K6_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "loadtest", "constant_work.js")

PERIODS = {
    "@cycles": 100000,
    "@cache_misses": 1000,
    "@branch_misses": 1000,
    "@page_faults": 1,
    "@ctx_switches": 1
}

def run_cmd(cmd):
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def parse_map_dump(output):
    results = {k: 0 for k in PERIODS.keys()}
    for line in output.split("\n"):
        match = re.match(r"(@[a-z_]+):\s+(\d+)", line)
        if match:
            key, val = match.groups()
            if key in results:
                results[key] = int(val) * PERIODS[key]
    return results

def run_measurement(pid, with_profiler=False):
    if with_profiler:
        print("    Starting perf record on API...")
        run_cmd("sudo pkill -9 -x perf 2>/dev/null")
        run_cmd(f"sudo taskset -c {PERF_RECORD_CPU} perf record -g -e cycles:k -c 100000 -p {pid} -o /tmp/perf-constant.data >/dev/null 2>&1 &")
        time.sleep(1)
        
    print("    Starting eBPF measurement...")
    # Start bpftrace
    # Taskset is omitted for bpftrace (runs in kernel space on target CPU)
    bpftrace = subprocess.Popen(["sudo", "bpftrace", EBPF_SCRIPT, str(pid)], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(5) # Let it attach
    
    print("    Running k6 constant load test (100,000 reqs)...")
    k6_res = run_cmd(f"k6 run {K6_SCRIPT}")
    
    # Send SIGINT to trigger map dump
    bpftrace.send_signal(signal.SIGINT)
    stdout, stderr = bpftrace.communicate()
    
    if with_profiler:
        run_cmd("sudo pkill -9 -x perf 2>/dev/null")
        
    return parse_map_dump(stdout)

def format_si(n):
    """Format large numbers with SI suffixes: 1000 → 1.0 K, 1000000 → 1.0 M."""
    n = float(n)
    if abs(n) >= 1e9:
        return f"{n / 1e9:.1f} B"
    elif abs(n) >= 1e6:
        return f"{n / 1e6:.1f} M"
    elif abs(n) >= 1e3:
        return f"{n / 1e3:.1f} K"
    else:
        return f"{n:.0f}"

def main():
    print("=== Go API Constant Work Measurement ===")
    
    # Get Go API PID
    api_pid = run_cmd("pgrep -f '^/api$' | head -n1").stdout.strip()
    if not api_pid:
        print("ERROR: Go API is not running. Please start docker compose.")
        return
        
    print(f"Target PID: {api_pid}")
    
    # 1. Baseline
    print("\nPhase A: Baseline Measurement (no profiler)")
    base_results = run_measurement(api_pid, with_profiler=False)
    
    # Wait for things to settle
    time.sleep(3)
    
    # 2. Tool
    print("\nPhase B: Tool Overhead Measurement (perf record active)")
    tool_results = run_measurement(api_pid, with_profiler=True)
    
    # 3. Calculate per request
    REQUESTS = 100000
    
    print("\n")
    print("=" * 85)
    print("  Table: Absolute Per-Request Overhead of perf record -g -c 100K")
    print("         on Go API Microservice (100 K Requests, Constant Work)")
    print("")
    print("  Target: Go API container (unpinned) | Inner tool: perf record on CPU 7")
    print("  Outer ruler: bpftrace (eBPF) on CPU 14 | Load: k6 with 50 VUs")
    print("=" * 85)
    print(f"{'Metric':<20} | {'Base Total':<15} | {'Tool Total':<15} | {'Per-Req Overhead':<15}")
    print("-" * 85)
    
    display_names = {
        "@cycles": "CPU Cycles",
        "@cache_misses": "L3 Cache Misses",
        "@branch_misses": "Branch Misses",
        "@page_faults": "Page Faults",
        "@ctx_switches": "Context Switches"
    }
    
    cycle_overhead = 0
    for key, name in display_names.items():
        b_val = base_results.get(key, 0)
        t_val = tool_results.get(key, 0)
        
        diff = t_val - b_val
        per_req = diff / REQUESTS if diff > 0 else 0
        if key == "@cycles":
            cycle_overhead = per_req
        
        print(f"{name:<20} | {format_si(b_val):<15} | {format_si(t_val):<15} | +{format_si(per_req):<15}")
        
    print("-" * 85)
    print(f"\n  Key Observation: For every 1 request to the Go API, perf record consumed")
    print(f"  an additional {format_si(cycle_overhead)} CPU cycles of overhead.")
    print("=" * 85)
    
if __name__ == "__main__":
    main()
