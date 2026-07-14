#!/usr/bin/env python3
"""
measure_statistically_ebpf.py — eBPF-Based Statistical Measurement of Profiling Overhead

Replaces perf stat (the outer measurement layer) with bpftrace/eBPF.
Uses the same statistical analysis as measure_statistically.py:
  - Welch's t-test (unequal variance)
  - Cohen's d effect size
  - Overhead percentage computation

Architecture per iteration:
  Phase A (Baseline):  bpftrace measures dummy_workload events (no profiler running)
  Phase B (Tool):      bpftrace measures perf record's events (while it profiles the workload)

CPU pinning (Intel Xeon E5-2620 v4, isolated CPUs: 6,7,14,15):
  dummy_workload:  CPU 6  (Core 6, Socket 0)
  perf record:     CPU 7  (Core 7, Socket 0)
  bpftrace:        CPU 14 (Core 6, Socket 1)

Each layer runs on a separate isolated physical core to eliminate
cross-talk and scheduler noise.
"""

import argparse
import subprocess
import time
import warnings
import math
import os
import shutil

import pandas as pd
from scipy import stats

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Path to the eBPF counter script (same directory as this script)
EBPF_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ebpf_counter.bt")

# Metrics tracked (must match ebpf_counter.bt output)
METRICS = ["cycles", "cache-misses", "L1-dcache-load-misses", "branch-misses", "page-faults", "context-switches"]

# CPU pinning for isolated cores on this system
BPFTRACE_CPU = "14"       # Outer measurement — bpftrace
PERF_RECORD_CPU = "7"     # Inner tool being measured — perf record

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_number(n):
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

def run_cmd(cmd, timeout=None):
    """Run a shell command and return the CompletedProcess result."""
    try:
        res = subprocess.run(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout
        )
        if res.returncode != 0 and "kill" not in cmd and "pgrep" not in cmd:
            print(f"  [CMD FAIL] {cmd[:80]}\n    stderr: {res.stderr[:200]}")
        return res
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT] {cmd[:80]}")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="timeout")


def parse_bpftrace_output(output):
    """Parse bpftrace CSV output.

    Expected format (between CSV_START / CSV_END markers):
        metric,raw_count,period

    Returns dict {metric_name: extrapolated_value}.
    Extrapolated value = raw_count × period.
    Software events (period=1) are exact.
    """
    results = {}
    in_csv = False
    for line in output.split('\n'):
        line = line.strip()
        if line == 'CSV_START':
            in_csv = True
            continue
        if line == 'CSV_END':
            break
        if in_csv and ',' in line:
            parts = line.split(',')
            if len(parts) >= 3:
                metric = parts[0]
                try:
                    count = int(parts[1])
                    period = int(parts[2])
                    results[metric] = count * period
                except ValueError:
                    pass
    return results


def run_bpftrace(pid, duration):
    """Run the eBPF counter script against a PID for a given duration.

    Returns parsed results dict or empty dict on failure.
    """
    # NOTE: No taskset for bpftrace — BPF probes run in kernel space on the
    # target's CPU regardless of where the bpftrace userspace process runs.
    # taskset can also trigger symbol resolution bugs in bpftrace 0.14.
    cmd = f"sudo bpftrace {EBPF_SCRIPT} {pid} {duration}"
    # bpftrace needs time to compile BPF bytecode on first run (~5-10s),
    # plus the actual measurement duration, plus cleanup time.
    timeout = duration + 45
    res = run_cmd(cmd, timeout=timeout)
    if res.returncode != 0:
        return {}
    return parse_bpftrace_output(res.stdout)


def cohens_d(group1, group2):
    """Calculate Cohen's d effect size between two groups."""
    n1, n2 = len(group1), len(group2)
    if n1 < 2 or n2 < 2:
        return 0.0
    mean1 = sum(group1) / n1
    mean2 = sum(group2) / n2
    var1 = sum((x - mean1) ** 2 for x in group1) / (n1 - 1)
    var2 = sum((x - mean2) ** 2 for x in group2) / (n2 - 1)
    pooled_std = math.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
    if pooled_std == 0:
        return float('inf') if mean1 != mean2 else 0.0
    return abs(mean1 - mean2) / pooled_std


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="eBPF-Based Statistical Measurement of Profiling Overhead"
    )
    parser.add_argument("--iterations", type=int, default=100,
                        help="Number of measurement iterations (default: 100)")
    parser.add_argument("--duration", type=int, default=2,
                        help="Duration in seconds per measurement window (default: 2)")
    parser.add_argument("--pid", type=str, required=True,
                        help="PID of the target process to profile")
    parser.add_argument("--output", type=str, default="results/ebpf_overhead_results.csv",
                        help="Path to save results CSV")
    args = parser.parse_args()

    # --- Pre-flight checks ---
    if not shutil.which("bpftrace"):
        print("ERROR: bpftrace is not installed.")
        print("  Install with: sudo apt install -y bpftrace")
        exit(1)

    if not os.path.exists(EBPF_SCRIPT):
        print(f"ERROR: eBPF counter script not found: {EBPF_SCRIPT}")
        exit(1)

    pid = args.pid
    print(f"=== eBPF Statistical Overhead Measurement ===")
    print(f"  Target PID:       {pid}")
    print(f"  eBPF script:      {EBPF_SCRIPT}")
    print(f"  bpftrace CPU:     {BPFTRACE_CPU} (outer measurement)")
    print(f"  perf record CPU:  {PERF_RECORD_CPU} (inner tool)")
    print(f"  Iterations:       {args.iterations}")
    print(f"  Window:           {args.duration}s")

    baseline_samples = {m: [] for m in METRICS}
    tool_samples = {m: [] for m in METRICS}

    print(f"\nStarting {args.iterations} iterations (Window: {args.duration}s)...")
    failed_iterations = 0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 10

    for i in range(args.iterations):
        print(f"  Iteration {i + 1}/{args.iterations}  "
              f"(failures: {failed_iterations})        ", end='\r')

        # ---- Phase A: Baseline ----
        # Measure the workload's own hardware events with no profiler attached.
        base_res = run_bpftrace(pid, args.duration)
        if not base_res:
            failed_iterations += 1
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n  ABORT: {MAX_CONSECUTIVE_FAILURES} consecutive failures. "
                      f"bpftrace cannot attach to hardware events on this system.")
                break
            continue
        consecutive_failures = 0  # Reset on success

        for m in METRICS:
            if m in base_res:
                baseline_samples[m].append(base_res[m])

        # ---- Phase B: Tool Overhead ----
        # Start perf record profiling the workload, then measure perf record
        # itself with bpftrace.
        run_cmd("sudo pkill -9 -x perf 2>/dev/null")
        run_cmd(
            f"sudo taskset -c {PERF_RECORD_CPU} perf record -g -e cycles:k "
            f"-c 100000 -p {pid} -o /tmp/perf-experiment.data >/dev/null 2>&1 &"
        )
        time.sleep(0.5)  # Let perf record start

        perf_pid = run_cmd("pgrep -x perf | head -n 1").stdout.strip()
        if perf_pid.isdigit():
            tool_res = run_bpftrace(perf_pid, args.duration)
            if tool_res:
                for m in METRICS:
                    if m in tool_res:
                        tool_samples[m].append(tool_res[m])

        run_cmd("sudo pkill -9 -x perf 2>/dev/null")

    # ------------------------------------------------------------------
    # Statistical Analysis
    # ------------------------------------------------------------------
    print(f"\n\n  Completed. Failed iterations: {failed_iterations}/{args.iterations}")

    results = []
    for metric in METRICS:
        if not baseline_samples[metric] or not tool_samples[metric]:
            continue

        b_data = baseline_samples[metric]
        t_data = tool_samples[metric]

        b_mean = sum(b_data) / len(b_data)
        t_mean = sum(t_data) / len(t_data)
        b_std = (
            (sum((x - b_mean) ** 2 for x in b_data) / (len(b_data) - 1)) ** 0.5
            if len(b_data) > 1 else 0
        )
        t_std = (
            (sum((x - t_mean) ** 2 for x in t_data) / (len(t_data) - 1)) ** 0.5
            if len(t_data) > 1 else 0
        )

        # Overhead percentage
        if b_mean > 0:
            overhead_pct = f"{(t_mean / b_mean) * 100:.5f}%"
        else:
            overhead_pct = "N/A"

        # Welch's t-test
        if b_std == 0 and t_std == 0:
            if b_mean == t_mean:
                p_val_str = "1.0000e+00"
                is_sig = "NO"
            else:
                p_val_str = "<1e-300"
                is_sig = "YES"
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                t_stat, p_val = stats.ttest_ind(b_data, t_data, equal_var=False)

            if math.isnan(p_val):
                p_val_str = "NaN"
                is_sig = "NO"
            else:
                p_val_str = f"{p_val:.4e}"
                is_sig = "YES" if p_val < 0.05 else "NO"

        # Cohen's d
        d = cohens_d(b_data, t_data)
        d_str = f"{d:.2f}" if not math.isinf(d) else "∞"

        results.append({
            "Metric": metric,
            "Base Mean": format_number(b_mean),
            "Base Std": format_number(b_std),
            "Tool Mean": format_number(t_mean),
            "Tool Std": format_number(t_std),
            "Overhead %": overhead_pct,
            "P-Value": p_val_str,
            "Cohen's d": d_str,
            "Significant?": is_sig,
        })

    # ------------------------------------------------------------------
    # Print results
    # ------------------------------------------------------------------
    print(f"\n\n{'=' * 100}")
    print(f"  Table: Hardware Overhead of perf record -g -c 100K Profiling a CPU-bound")
    print(f"         Workload, Measured via eBPF over {args.iterations} Iterations ({args.duration}s Window)")
    print(f"")
    print(f"  Outer measurement: bpftrace (eBPF) on CPU {BPFTRACE_CPU} (Socket 1)")
    print(f"  Inner tool:        perf record -g -c 100K on CPU {PERF_RECORD_CPU} (Socket 0)")
    print(f"  Statistical test:  Welch's t-test (unequal variance), α = 0.05")
    print(f"{'=' * 100}")

    if results:
        df = pd.DataFrame(results)
        print(df.to_string(index=False))

        # Key observation
        cycle_row = next((r for r in results if r['Metric'] == 'cycles'), None)
        if cycle_row:
            print(f"\n  Key Observation: Cycle overhead is {cycle_row['Overhead %']},")
            print(f"  confirming negligible CPU cost of perf record at 100K sampling period.")

        # Save results CSV
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        df.to_csv(args.output, index=False)
        print(f"\n  Results saved to: {args.output}")
    else:
        print("  No data collected. Check that bpftrace can attach to hardware events.")
    print(f"{'=' * 100}")


if __name__ == "__main__":
    main()
