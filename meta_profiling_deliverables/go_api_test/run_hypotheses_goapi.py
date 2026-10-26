#!/usr/bin/env python3
"""
run_hypotheses_goapi.py — Hypothesis Testing for Go API (H1 + H4)

Uses constant-work methodology (fixed request count per iteration)
and perf stat as the outer measurement ruler. perf stat wraps the load
generator (perf stat -p PID -- hey -n N ...), so the counting window is
exactly the N requests.

H1 (Go API portion): Measures perf record's own overhead while profiling
    the Go API under load, plus the API's own cost without a profiler
    (workload_baseline). Compare with dummy workload results to test
    "constant absolute cost" hypothesis.

H4 (Production Negligibility): Measures the Go API's own hardware counters
    WITH and WITHOUT perf record. Tests whether overhead < 1% using TOST.

CPU pinning:
  Go API container:   NOT pinned (realistic production)
  perf record:        CPU 7  (Core 7, Socket 0)
  perf stat ruler:    CPU 14 (Core 6, Socket 1)

Usage:
  sudo python3 run_hypotheses_goapi.py --pilot            # 50 iterations
  sudo python3 run_hypotheses_goapi.py --iterations 200   # full run
  sudo python3 run_hypotheses_goapi.py --hypothesis H4    # H4 only
"""

import argparse
import subprocess
import time
import os
import sys
import csv
from datetime import datetime

# Add common/ to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
from perf_stat_ruler import measure_pid_during_command, check_perf_stat_available

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PERF_RECORD_CPU = "7"
RULER_CPU = "14"  # perf stat's userspace process runs here

REQUESTS_PER_ITER = 1000
WARMUP_REQUESTS = 500
CONCURRENCY = 10
API_URL = "http://localhost/compute"

# perf stat events — 3 hardware + 2 software, no PMU multiplexing
PERF_STAT_EVENTS = [
    "cycles",
    "L1-dcache-load-misses",
    "branch-misses",
    "context-switches",
    "page-faults",
]

SESSION_ID = datetime.now().strftime("%Y%m%d_%H%M%S")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd, timeout=120):
    try:
        return subprocess.run(
            cmd, shell=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "timeout")


def get_api_pid():
    res = run_cmd("pgrep -f '^/api$' | head -n1")
    pid = res.stdout.strip()
    if pid.isdigit():
        return pid
    res = run_cmd("docker inspect --format '{{.State.Pid}}' perf-go-api")
    pid = res.stdout.strip()
    if pid.isdigit() and pid != "0":
        return pid
    return None


def hey_cmd(n):
    return f"hey -n {n} -c {CONCURRENCY} -q 500 {API_URL}"


def send_requests(n):
    """Send exactly n requests using hey."""
    return run_cmd(hey_cmd(n), timeout=300)


def warmup():
    send_requests(WARMUP_REQUESTS)
    time.sleep(1)


def start_perf_record(target_pid, flags="-g -c 100000"):
    run_cmd("pkill -9 -x perf 2>/dev/null")
    time.sleep(0.2)
    cmd = (
        f"taskset -c {PERF_RECORD_CPU} perf record {flags} "
        f"-e cycles:k -p {target_pid} -o /tmp/perf-hyp-goapi.data "
        f">/dev/null 2>&1 &"
    )
    run_cmd(cmd)
    time.sleep(1)
    res = run_cmd("pgrep -x perf | head -n 1")
    pid = res.stdout.strip()
    return pid if pid.isdigit() else None


def stop_perf_record():
    run_cmd("pkill -9 -x perf 2>/dev/null")
    time.sleep(0.2)


def measure_constant_work(target_pid):
    """Count TARGET_PID's hardware events over exactly REQUESTS_PER_ITER requests.

    perf stat runs hey as its workload command, so counting starts before
    the first request and stops when hey exits after the last one — no
    idle tail and no attach race (supersedes the Issue 9 bpftrace fix).

    Returns dict of hardware event counts, or None on failure.
    """
    return measure_pid_during_command(
        target_pid, hey_cmd(REQUESTS_PER_ITER),
        ruler_cpu=RULER_CPU, events=PERF_STAT_EVENTS, timeout=300,
    )


# ---------------------------------------------------------------------------
# CSV Writer
# ---------------------------------------------------------------------------

class ResultsWriter:
    FIELDNAMES = [
        "session_id", "timestamp", "hypothesis", "workload", "config_g",
        "config_c", "iteration", "run_type",
        "cycles", "cache_misses", "branch_misses",
        "page_faults", "context_switches", "requests_served"
    ]

    def __init__(self, output_path):
        self.output_path = output_path
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        self.file = open(output_path, 'w', newline='')
        self.writer = csv.DictWriter(self.file, fieldnames=self.FIELDNAMES)
        self.writer.writeheader()

    def write_row(self, hypothesis, config_g, config_c, iteration,
                  run_type, results, requests=0):
        row = {
            "session_id": SESSION_ID,
            "timestamp": datetime.now().isoformat(),
            "hypothesis": hypothesis,
            "workload": "goapi",
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
            "requests_served": requests,
        }
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()


# ---------------------------------------------------------------------------
# H1: Constant Absolute Cost (Go API portion)
# ---------------------------------------------------------------------------

def run_h1_goapi(api_pid, writer, iterations):
    """Measure perf record's overhead while profiling the Go API under load.
    Also measures the Go API's own cost without a profiler (workload_baseline)
    so the tool/workload ratio is available for the Go API too.
    """
    print(f"\n{'='*70}")
    print(f"  H1 (Go API): Constant Absolute Cost ({iterations} iterations)")
    print(f"  Constant work: {REQUESTS_PER_ITER} requests per iteration")
    print(f"  Outer ruler: perf stat (CPU {RULER_CPU})")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        print(f"  H1 iter {i+1}/{iterations} (fail: {failures})    ", end='\r')

        # Phase A: API's own cost, no profiler
        warmup()
        base_results = measure_constant_work(int(api_pid))
        if base_results:
            writer.write_row("H1", "off", 0, i+1,
                             "workload_baseline", base_results, REQUESTS_PER_ITER)

        # Phase B: start perf record on the API
        perf_pid = start_perf_record(api_pid, "-g -c 100000")
        if not perf_pid:
            failures += 1
            stop_perf_record()
            continue

        # Measure perf record's own cost (not the API's) during N requests
        results = measure_constant_work(int(perf_pid))
        if results:
            writer.write_row("H1", "on", 100000, i+1,
                             "tool_overhead", results, REQUESTS_PER_ITER)
        else:
            failures += 1

        stop_perf_record()
        time.sleep(2)

    print(f"\n  H1 (Go API) complete. Failures: {failures}/{iterations}")


# ---------------------------------------------------------------------------
# H4: Production Negligibility (TOST Equivalence)
# ---------------------------------------------------------------------------

def run_h4(api_pid, writer, iterations):
    """Measure Go API WITH and WITHOUT profiler (constant work).
    
    This measures the APPLICATION's own counters to capture total impact,
    including cache pollution from perf record.
    """
    print(f"\n{'='*70}")
    print(f"  H4: Production Negligibility ({iterations} iterations)")
    print(f"  Constant work: {REQUESTS_PER_ITER} requests per iteration")
    print(f"  Question: Is total overhead < 1% of Go API resource consumption?")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        print(f"  H4 iter {i+1}/{iterations} (fail: {failures})    ", end='\r')

        # ---- Phase A: BASELINE (no profiler) ----
        warmup()
        base_results = measure_constant_work(int(api_pid))
        if not base_results:
            failures += 1
            continue

        writer.write_row("H4", "off", 0, i+1,
                         "baseline", base_results, REQUESTS_PER_ITER)

        time.sleep(2)  # cooldown

        # ---- Phase B: PROFILED (perf record active) ----
        warmup()
        perf_pid = start_perf_record(api_pid, "-g -c 100000")
        if not perf_pid:
            # Never record a "profiled" row with no profiler running
            failures += 1
            stop_perf_record()
            continue

        prof_results = measure_constant_work(int(api_pid))
        if prof_results:
            writer.write_row("H4", "on", 100000, i+1,
                             "profiled", prof_results, REQUESTS_PER_ITER)
        else:
            failures += 1

        stop_perf_record()
        time.sleep(2)  # cooldown

    print(f"\n  H4 complete. Failures: {failures}/{iterations}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global REQUESTS_PER_ITER
    parser = argparse.ArgumentParser(
        description="Hypothesis Testing — Go API (H1 + H4, Constant Work)"
    )
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--pilot", action="store_true",
                        help="Pilot mode: 50 iterations")
    parser.add_argument("--hypothesis", type=str,
                        choices=["H1", "H4", "all"], default="all")
    parser.add_argument("--requests", type=int, default=REQUESTS_PER_ITER,
                        help=f"Requests per iteration (default: {REQUESTS_PER_ITER})")
    parser.add_argument("--output", type=str,
                        default=f"../results/hypotheses_goapi_{SESSION_ID}.csv")
    args = parser.parse_args()

    if args.pilot:
        args.iterations = 50

    REQUESTS_PER_ITER = args.requests

    api_pid = get_api_pid()
    if not api_pid:
        print("  ERROR: Go API not running. Start: cd ../.. && docker compose up -d")
        return

    # Check hey is installed
    if not run_cmd("which hey").stdout.strip():
        print("  ERROR: 'hey' not installed.")
        print("  Install: go install github.com/rakyll/hey@latest")
        return

    if not check_perf_stat_available(ruler_cpu=RULER_CPU):
        return

    print(f"{'='*70}")
    print(f"  Hypothesis Testing — Go API (Constant Work)")
    print(f"  Session:      {SESSION_ID}")
    print(f"  Go API PID:   {api_pid}")
    print(f"  Hypothesis:   {args.hypothesis}")
    print(f"  Iterations:   {args.iterations}")
    print(f"  Requests/iter: {REQUESTS_PER_ITER}")
    print(f"  Outer ruler:  perf stat (CPU {RULER_CPU})")
    print(f"  Output:       {args.output}")
    print(f"{'='*70}")

    writer = ResultsWriter(args.output)

    try:
        if args.hypothesis in ("all", "H1"):
            run_h1_goapi(api_pid, writer, args.iterations)
        if args.hypothesis in ("all", "H4"):
            run_h4(api_pid, writer, args.iterations)
    except KeyboardInterrupt:
        print("\n\n  ⚠️ Interrupted! Saving partial results...")
    finally:
        writer.close()
        stop_perf_record()

    print(f"\n{'='*70}")
    print(f"  ✅ COMPLETE — Results: {args.output}")
    print(f"  Next: python3 ../common/analyze_hypotheses.py --goapi {args.output}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
