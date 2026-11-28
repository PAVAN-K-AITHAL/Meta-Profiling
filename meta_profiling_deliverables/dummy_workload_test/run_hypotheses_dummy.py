#!/usr/bin/env python3
"""
run_hypotheses_dummy.py — Hypothesis testing on CPU-bound workloads (CPU 6)

Measurement methodology (two-sided meta-profiling):
  Every profiled window is measured from BOTH sides at once, with perf stat
  in counting mode as the outer ruler (exact PMU reads, no sampling):

    tool_overhead      perf stat -p <perf record PID>
                       perf record's own process: ring-buffer draining,
                       perf.data writes, its syscalls. Runs on CPU 7.

    workload_profiled  perf stat -C 6, in the same window
                       What profiling does to the workload: the sampling
                       interrupt, callchain unwinding and perf's other kernel
                       work all run on CPU 6, in the workload's time.

  plus, before perf record starts:

    workload_baseline  perf stat -C 6 with no profiler (same events)

  The window length is fixed and the workload never stops, so total time on
  CPU 6 is constant. Profiling cost therefore shows up as LOST WORK:
    slowdown = 1 - instructions:u(profiled) / instructions:u(baseline)
  and is split by where CPU 6's cycles went:
    user cycles lost  = cycles:u(base) - cycles:u(prof)
    kernel cycles     = cycles:k(prof) - cycles:k(base)
    uncounted cycles  = (u+k)(base) - (u+k)(prof)
  Intel's PMU interrupt handler disables all counters while it runs, so its
  own cycles are invisible to every counter on CPU 6; they appear only as
  uncounted cycles. Measuring perf record's PID alone (the old method) sees
  none of the CPU 6 cost.

Hypotheses:
  H1   perf record -g -c 100K on one workload (absolute cost, H4 ratio)
  H1W  cross-workload: dummy + synth_wl with controlled stack depth and
       working-set size (does per-sample cost depend on the workload?)
  H2   sampling period sweep (-c), randomized block design
  H3   call-graph mode ablation: none / frame pointer (-g) / LBR / DWARF

CPU pinning (Intel Xeon E5-2620 v4, isolated: 6,7,14,15,22,23,30,31):
  Workload:               CPU 6  (Core 6, Socket 0)
  perf record:            CPU 7  (Core 7, Socket 0)
  perf stat (ruler):      CPU 14 (Core 6, Socket 1)

Usage:
  sudo python3 run_hypotheses_dummy.py --pilot                 # 50 iterations, H1+H2+H3
  sudo python3 run_hypotheses_dummy.py --hypothesis H2 --iterations 50
  sudo python3 run_hypotheses_dummy.py --hypothesis H1W --iterations 30
  sudo python3 run_hypotheses_dummy.py --hypothesis H3 --workload synth --depth 64
"""

import argparse
import csv
import os
import random
import subprocess
import sys
import time
from datetime import datetime

# Add common/ to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
from perf_stat_ruler import (
    measure_pid_perf_stat, measure_cpu_perf_stat, measure_concurrently,
    check_perf_stat_available,
)
from measure_statistically_ebpf import format_number
from perf_record_stats import (stop_and_read, stop_perf_record_graceful,
                               configure_sample_rate_limits)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKLOAD_CPU = "6"
PERF_RECORD_CPU = "7"
RULER_CPU = "14"  # perf stat's userspace processes run here
DURATION = 5  # seconds per measurement window
PERF_DATA = "/tmp/perf-hypothesis.data"
HERE = os.path.dirname(os.path.abspath(__file__))

# PMU budget (Broadwell, HT on): 4 general-purpose counters per logical CPU
# plus fixed counters for instructions and cycles. Software events
# (context-switches, page-faults, task-clock) use no counter.
# Run with kernel.nmi_watchdog=0, or the watchdog takes one counter.

# perf record's own process (counted on CPU 7's PMU; perf record's sampling
# counter is on CPU 6). Only fixed-counter and software events: the
# Nov 28 pilot saw L1-dcache-load-misses and branch-misses multiplexed here
# while CPU 6 was counted at the same time. cycles and instructions each have
# a dedicated fixed counter, so they cannot be multiplexed.
TOOL_EVENTS = [
    "cycles",
    "instructions",
    "context-switches",
    "page-faults",
    "task-clock",
]

# Workload CPU, no profiler running: instructions:u on a fixed counter,
# 4 events on the general-purpose counters.
BASELINE_EVENTS = [
    "instructions:u",
    "cycles:u",
    "cycles:k",
    "L1-dcache-load-misses",
    "branch-misses",
    "context-switches",
    "page-faults",
]

# Workload CPU while perf record samples it: perf record's own sampling
# counter needs one counter on CPU 6, so branch-misses is dropped to stay
# within 4. A multiplexed measurement is rejected, never extrapolated.
PROFILED_EVENTS = [
    "instructions:u",
    "cycles:u",
    "cycles:k",
    "L1-dcache-load-misses",
    "context-switches",
    "page-faults",
]

# H2: sampling periods (cycles; 2.1 GHz with Turbo off). 5000 is past the
# cliff (CPU 6 does almost nothing but take samples). 500K ≈ perf's default
# -F 4000; 2M ≈ 1 kHz; 20M ≈ 100 Hz, the rate continuous profilers use.
H2_CONFIGS = [5000, 7500, 10000, 20000, 50000, 100000, 500000, 1000000,
              2000000, 20000000]

# H3: call-graph modes at -c 100K (config_g label, perf record flags)
H3_CONFIGS = [
    ("off",   "-c 100000"),
    ("on",    "-g -c 100000"),                    # frame-pointer unwinding
    ("lbr",   "--call-graph lbr -c 100000"),      # hardware call stack (Haswell+)
    ("dwarf", "--call-graph dwarf -c 100000"),    # copies 8 KB of stack per sample
]

# H1W: cross-workload set. synth_wl = (stack depth, working set MB)
H1W_WORKLOADS = [
    {"kind": "dummy_workload"},
    {"kind": "synth", "depth": 1,   "size_mb": 1},
    {"kind": "synth", "depth": 16,  "size_mb": 1},
    {"kind": "synth", "depth": 64,  "size_mb": 1},
    {"kind": "synth", "depth": 120, "size_mb": 1},
    {"kind": "synth", "depth": 1,   "size_mb": 256},
    {"kind": "synth", "depth": 120, "size_mb": 256},
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


def workload_label(spec):
    if spec["kind"] == "synth":
        return f"synth_d{spec['depth']}_s{spec['size_mb']}"
    return spec["kind"]


_built = set()


def build(name, flags):
    """Compile <name>.c once per run. Returns the binary path or None."""
    binary = os.path.join(HERE, name)
    if name in _built:
        return binary
    src = os.path.join(HERE, f"{name}.c")
    res = run_cmd(f"gcc {flags} '{src}' -o '{binary}'")
    if res.returncode != 0:
        print(f"  Could not build {name}: {res.stderr.strip()}")
        return None
    _built.add(name)
    return binary


def start_workload(spec):
    """Start the workload described by spec on the isolated CPU.
    Returns its PID or None. Never substitutes another workload.

    dummy_workload: allocates once, then loops forever (stationary).
    synth: synth_wl <depth> <size_mb>, stationary; waits for its ready file
        so the set-up phase is never measured.
    lat_mem_rd: restarted in a loop and sweeps array sizes, so its
        behaviour varies over time. Kept for comparison with older runs.
    """
    stop_workload()
    kind = spec["kind"]

    if kind == "dummy_workload":
        # Frame pointers are for perf -g; -O0 matches all earlier runs
        binary = build("dummy_workload", "-O0 -fno-omit-frame-pointer")
        if not binary:
            return None
        os.system(f"nohup taskset -c {WORKLOAD_CPU} '{binary}' > /dev/null 2>&1 &")
        time.sleep(3)  # let the one-time array fill finish
        pid = run_cmd("pgrep -x dummy_workload | head -n 1").stdout.strip()
        return pid if pid.isdigit() else None

    if kind == "synth":
        binary = build("synth_wl", "-O2 -fno-omit-frame-pointer -fno-optimize-sibling-calls")
        if not binary:
            return None
        ready = "/tmp/synth_wl.ready"
        if os.path.exists(ready):
            os.remove(ready)
        os.system(f"nohup taskset -c {WORKLOAD_CPU} '{binary}' "
                  f"{spec['depth']} {spec['size_mb']} > /dev/null 2>&1 &")
        deadline = time.time() + 120
        while not os.path.exists(ready) and time.time() < deadline:
            time.sleep(0.2)
        if not os.path.exists(ready):
            print(f"  synth_wl did not become ready within 120 s")
            return None
        time.sleep(1)  # settle caches after set-up
        pid = run_cmd("pgrep -x synth_wl | head -n 1").stdout.strip()
        return pid if pid.isdigit() else None

    lat_mem_cmd = None
    if run_cmd("which lat_mem_rd").returncode == 0:
        lat_mem_cmd = "lat_mem_rd"
    elif os.path.exists("/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd"):
        lat_mem_cmd = "/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd"
    if not lat_mem_cmd:
        return None
    wrapper = f"while true; do {lat_mem_cmd} -t 128M 512; done"
    os.system(f"nohup taskset -c {WORKLOAD_CPU} bash -c '{wrapper}' > /dev/null 2>&1 &")
    time.sleep(3)
    pid = run_cmd("pgrep -x lat_mem_rd | head -n 1").stdout.strip()
    return pid if pid.isdigit() else None


def stop_workload():
    # Match lat_mem_rd's arguments, not just its name: "-f lat_mem_rd" would
    # also match (and kill) this script run with "--workload lat_mem_rd".
    run_cmd("pkill -9 -f '[l]at_mem_rd -t 128M'")
    run_cmd("pkill -9 -x lat_mem_rd")
    run_cmd("pkill -9 -x dummy_workload")
    run_cmd("pkill -9 -x synth_wl")
    time.sleep(0.5)


def start_perf_record(flags="-g -c 100000"):
    """Start perf record profiling CPU 6 (where the workload runs).
    Returns the perf PID or None.
    """
    run_cmd("pkill -9 -x perf 2>/dev/null")
    time.sleep(0.3)
    # -C profiles the whole CPU, not a PID — robust against pid churn.
    # -e cycles (user + kernel) makes the sample rate deterministic:
    # CPU freq / c.
    cmd = (
        f"nohup taskset -c {PERF_RECORD_CPU} perf record {flags} "
        f"-e cycles -C {WORKLOAD_CPU} -o {PERF_DATA} "
        f"> /dev/null 2>&1 &"
    )
    os.system(cmd)
    time.sleep(1)  # Let perf record stabilize
    pid = run_cmd("pgrep -x perf | head -n 1").stdout.strip()
    return pid if pid.isdigit() else None


def stop_perf_record():
    stop_perf_record_graceful()


# ---------------------------------------------------------------------------
# CSV Writer
# ---------------------------------------------------------------------------

class ResultsWriter:
    FIELDNAMES = [
        "session_id", "timestamp", "hypothesis", "workload", "depth",
        "size_mb", "config_g", "config_c", "iteration", "run_type",
        "cycles", "cycles_u", "cycles_k", "instructions_u",
        "cache_misses", "branch_misses", "page_faults", "context_switches",
        "task_clock_ms",
        # perf record data quality (profiled rows; >0 = invalid run)
        "lost_events", "throttle_events", "samples",
    ]

    def __init__(self, output_path):
        self.output_path = output_path
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        self.file = open(output_path, 'w', newline='')
        self.writer = csv.DictWriter(self.file, fieldnames=self.FIELDNAMES)
        self.writer.writeheader()

    def write_row(self, hypothesis, spec, config_g, config_c,
                  iteration, run_type, results):
        cycles = results.get("cycles", "")
        if cycles == "" and "cycles_u" in results and "cycles_k" in results:
            # CPU rows count u and k separately; their sum is the cycles
            # the PMU saw on CPU 6 (excludes the PMU interrupt handler)
            cycles = results["cycles_u"] + results["cycles_k"]
        row = {
            "session_id": SESSION_ID,
            "timestamp": datetime.now().isoformat(),
            "hypothesis": hypothesis,
            "workload": workload_label(spec),
            "depth": spec.get("depth", ""),
            "size_mb": spec.get("size_mb", ""),
            "config_g": config_g,
            "config_c": config_c,
            "iteration": iteration,
            "run_type": run_type,
            # Missing metrics are left empty (NaN), never 0, so they
            # cannot bias means. cache_misses = L1-dcache-load-misses.
            "cycles": cycles,
            "cycles_u": results.get("cycles_u", ""),
            "cycles_k": results.get("cycles_k", ""),
            "instructions_u": results.get("instructions_u", ""),
            "cache_misses": results.get("L1_dcache_misses", ""),
            "branch_misses": results.get("branch_misses", ""),
            "page_faults": results.get("page_faults", ""),
            "context_switches": results.get("context_switches", ""),
            "task_clock_ms": results.get("task_clock_ms", ""),
            "lost_events": results.get("lost_events", ""),
            "throttle_events": results.get("throttle_events", ""),
            "samples": results.get("samples", ""),
        }
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def measure_workload_baseline(duration):
    """CPU 6 with no profiler running (CPU-wide: immune to PID churn)."""
    return measure_cpu_perf_stat(WORKLOAD_CPU, duration,
                                 ruler_cpu=RULER_CPU, events=BASELINE_EVENTS)


def measure_profiled_window(perf_pid, duration):
    """Count perf record's own PID and CPU 6 over the same window.
    Returns (tool_counts, workload_counts); either may be None."""
    tool, workload = measure_concurrently(
        (measure_pid_perf_stat, (int(perf_pid), duration),
         {"ruler_cpu": RULER_CPU, "events": TOOL_EVENTS}),
        (measure_cpu_perf_stat, (WORKLOAD_CPU, duration),
         {"ruler_cpu": RULER_CPU, "events": PROFILED_EVENTS}),
    )
    return tool, workload


def baseline_row(writer, hyp, spec, config_c, iteration, config_g="none"):
    baseline = measure_workload_baseline(DURATION)
    if baseline:
        writer.write_row(hyp, spec, config_g, config_c, iteration,
                         "workload_baseline", baseline)
    return baseline is not None


def profiled_rows(writer, hyp, spec, flags, config_g, config_c, iteration):
    """Run one profiled window and write its tool_overhead and
    workload_profiled rows. Returns True if both were recorded."""
    perf_pid = start_perf_record(flags)
    if not perf_pid:
        stop_perf_record()
        return False

    tool, workload = measure_profiled_window(perf_pid, DURATION)
    quality = stop_and_read(PERF_DATA)  # lost/throttle/sample counts
    if quality['lost_events'] or quality['throttle_events']:
        print(f"\n  ⚠️ {hyp} {workload_label(spec)} {config_g} -c {config_c} iter {iteration}: "
              f"lost={quality['lost_events']} throttle={quality['throttle_events']} — flagged")

    for run_type, data in (("tool_overhead", tool), ("workload_profiled", workload)):
        if data:
            data.update(quality)
            writer.write_row(hyp, spec, config_g, config_c, iteration, run_type, data)
    return bool(tool) and bool(workload)


# ---------------------------------------------------------------------------
# Hypotheses
# ---------------------------------------------------------------------------

def run_h1(writer, iterations, spec):
    """perf record -g -c 100K: baseline window, then profiled window."""
    print(f"\n{'='*70}")
    print(f"  H1: perf record -g -c 100K on {workload_label(spec)} ({iterations} iterations)")
    print(f"{'='*70}")
    failures = 0
    for i in range(iterations):
        print(f"  H1 iter {i+1}/{iterations} (fail: {failures})    ", end='\r')
        baseline_row(writer, "H1", spec, 100000, i + 1)
        time.sleep(1)
        if not profiled_rows(writer, "H1", spec, "-g -c 100000", "on", 100000, i + 1):
            failures += 1
        time.sleep(2)
    print(f"\n  H1 complete. Failures: {failures}/{iterations}")


def run_h2(writer, iterations, spec):
    """Sampling-period sweep, randomized block design: one baseline per
    block, then every -c value in shuffled order."""
    configs_str = ', '.join(format_number(c) for c in H2_CONFIGS)
    print(f"\n{'='*70}")
    print(f"  H2: period sweep on {workload_label(spec)} "
          f"({iterations} blocks × {len(H2_CONFIGS)} configs)")
    print(f"  Configs: -c [{configs_str}]")
    print(f"{'='*70}")
    failures = 0
    for i in range(iterations):
        baseline_row(writer, "H2", spec, 0, i + 1)
        time.sleep(1)
        shuffled = H2_CONFIGS.copy()
        random.shuffle(shuffled)
        for c_val in shuffled:
            print(f"  H2 block {i+1}/{iterations}, -c {format_number(c_val)} "
                  f"(fail: {failures})    ", end='\r')
            if not profiled_rows(writer, "H2", spec, f"-g -c {c_val}", "on", c_val, i + 1):
                failures += 1
            time.sleep(2)
    print(f"\n  H2 complete. Failures: {failures}")


def run_h3(writer, iterations, spec, configs):
    """Call-graph mode ablation, paired blocks, order randomized per block."""
    print(f"\n{'='*70}")
    print(f"  H3: call-graph modes on {workload_label(spec)} ({iterations} blocks)")
    print(f"  Modes: {', '.join(label for label, _ in configs)} (all -c 100K)")
    print(f"{'='*70}")
    failures = 0
    for i in range(iterations):
        baseline_row(writer, "H3", spec, 100000, i + 1)
        time.sleep(1)
        shuffled = configs.copy()
        random.shuffle(shuffled)
        for label, flags in shuffled:
            print(f"  H3 block {i+1}/{iterations}, {label} (fail: {failures})    ", end='\r')
            if not profiled_rows(writer, "H3", spec, flags, label, 100000, i + 1):
                failures += 1
            time.sleep(2)
    print(f"\n  H3 complete. Failures: {failures}")


def run_h1w(writer, iterations, workloads):
    """Cross-workload: each block visits every workload in shuffled order,
    restarting it, so slow drift cannot masquerade as a workload effect."""
    print(f"\n{'='*70}")
    print(f"  H1W: cross-workload ({iterations} blocks × {len(workloads)} workloads)")
    print(f"  Workloads: {', '.join(workload_label(w) for w in workloads)}")
    print(f"{'='*70}")
    failures = 0
    for i in range(iterations):
        shuffled = workloads.copy()
        random.shuffle(shuffled)
        for spec in shuffled:
            print(f"  H1W block {i+1}/{iterations}, {workload_label(spec)} "
                  f"(fail: {failures})    ", end='\r')
            if not start_workload(spec):
                print(f"\n  Could not start {workload_label(spec)}")
                failures += 1
                continue
            baseline_row(writer, "H1W", spec, 100000, i + 1)
            time.sleep(1)
            if not profiled_rows(writer, "H1W", spec, "-g -c 100000", "on", 100000, i + 1):
                failures += 1
            time.sleep(2)
    stop_workload()
    print(f"\n  H1W complete. Failures: {failures}")


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def preflight(spec, h3_configs):
    """Check that every measurement works before hours of collection.
    Returns the H3 configs this machine supports, or None to abort."""
    if not check_perf_stat_available(ruler_cpu=RULER_CPU):
        return None

    watchdog = run_cmd("cat /proc/sys/kernel/nmi_watchdog").stdout.strip()
    if watchdog == "1":
        print("  ⚠️ kernel.nmi_watchdog=1 takes a PMU counter; profiled CPU 6 "
              "measurements may be multiplexed and rejected. Fix: "
              "sudo sysctl kernel.nmi_watchdog=0")

    print(f"\n  Pre-flight: baseline on CPU {WORKLOAD_CPU}...")
    base = measure_workload_baseline(DURATION)
    if not base:
        print("  ❌ Baseline measurement failed (see perf_stat_ruler message above).")
        return None
    for k, v in base.items():
        print(f"     {k}: {format_number(v)}")

    print(f"\n  Pre-flight: profiled window (perf record -g -c 100K)...")
    perf_pid = start_perf_record("-g -c 100000")
    if not perf_pid:
        print("  ❌ Could not start perf record. Check permissions (run with sudo).")
        return None
    tool, workload = measure_profiled_window(perf_pid, DURATION)
    quality = stop_and_read(PERF_DATA)
    if not tool or not workload:
        print("  ❌ Profiled window failed: "
              f"tool={'ok' if tool else 'FAILED'} workload={'ok' if workload else 'FAILED'}")
        print("     If 'REJECTED: counters multiplexed' is printed above, set "
              "kernel.nmi_watchdog=0.")
        return None
    print(f"  ✅ perf record process: {format_number(tool['cycles'])} cycles, "
          f"{tool.get('task_clock_ms', 0):.0f} ms CPU")
    slow = 1 - workload["instructions_u"] / base["instructions_u"]
    print(f"  ✅ workload slowdown: {slow*100:.2f}%  "
          f"(instructions:u {format_number(base['instructions_u'])} → "
          f"{format_number(workload['instructions_u'])})")
    uncounted = (base["cycles_u"] + base["cycles_k"]) - (workload["cycles_u"] + workload["cycles_k"])
    print(f"     uncounted cycles on CPU {WORKLOAD_CPU}: {format_number(uncounted)}; "
          f"samples written: {quality['samples']}")

    supported = []
    for label, flags in h3_configs:
        pid = start_perf_record(flags)
        if pid:
            supported.append((label, flags))
        else:
            print(f"  ⚠️ perf record {flags} does not start on this machine — "
                  f"dropping H3 mode '{label}'")
        stop_perf_record()
    return supported


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Hypothesis testing — CPU-bound workloads (H1, H1W, H2, H3)"
    )
    parser.add_argument("--iterations", type=int, default=200,
                        help="Iterations (blocks) per hypothesis (default: 200)")
    parser.add_argument("--pilot", action="store_true",
                        help="Pilot mode: 50 iterations")
    parser.add_argument("--hypothesis", type=str,
                        choices=["H1", "H1W", "H2", "H3", "all"], default="all",
                        help="Which hypothesis to run (default: all = H1+H2+H3)")
    parser.add_argument("--workload", choices=["dummy_workload", "synth", "lat_mem_rd"],
                        default="dummy_workload",
                        help="Workload on CPU 6 for H1/H2/H3 (default: dummy_workload)")
    parser.add_argument("--depth", type=int, default=64,
                        help="synth_wl stack depth (default: 64)")
    parser.add_argument("--size-mb", type=int, default=1,
                        help="synth_wl working set in MB (default: 1)")
    parser.add_argument("--output", type=str,
                        default=f"../results/hypotheses_dummy_{SESSION_ID}.csv",
                        help="Output CSV path")
    args = parser.parse_args()

    if args.pilot:
        args.iterations = 50
        print("  🧪 PILOT MODE: 50 iterations per hypothesis\n")
    iters = args.iterations

    spec = {"kind": args.workload}
    if args.workload == "synth":
        spec.update(depth=args.depth, size_mb=args.size_mb)

    configure_sample_rate_limits()

    first = H1W_WORKLOADS[0] if args.hypothesis == "H1W" else spec
    print("  Starting workload...")
    if not start_workload(first):
        print(f"  FATAL: Could not start {workload_label(first)}.")
        print("  dummy_workload/synth need gcc; lat_mem_rd needs: sudo apt install -y lmbench")
        sys.exit(1)
    print(f"  Workload: {workload_label(first)} (CPU {WORKLOAD_CPU})")
    if first["kind"] == "synth":
        print(f"  synth_wl: {open('/tmp/synth_wl.ready').read().strip()}")

    h3_configs = preflight(first, H3_CONFIGS)
    if h3_configs is None:
        stop_workload()
        stop_perf_record()
        sys.exit(1)

    # Time estimate: window + start/settle + stop/report + cooldown
    win = DURATION + 1
    prof = 1 + win + 4 + 2
    time_map = {
        "H1": iters * (win + 1 + prof),
        "H2": iters * (win + 1 + len(H2_CONFIGS) * prof),
        "H3": iters * (win + 1 + len(h3_configs) * prof),
        "H1W": iters * len(H1W_WORKLOADS) * (5 + win + 1 + prof),
    }
    chosen = ["H1", "H2", "H3"] if args.hypothesis == "all" else [args.hypothesis]
    est_min = sum(time_map[h] for h in chosen) / 60

    print(f"\n{'='*70}")
    print(f"  Session:      {SESSION_ID}")
    print(f"  Hypothesis:   {args.hypothesis}")
    print(f"  Iterations:   {iters} per hypothesis")
    print(f"  Window:       {DURATION}s")
    print(f"  Est. time:    ~{est_min:.0f} minutes")
    print(f"  Output:       {args.output}")
    print(f"{'='*70}")

    writer = ResultsWriter(args.output)
    try:
        if args.hypothesis == "H1W":
            run_h1w(writer, iters, H1W_WORKLOADS)
        if args.hypothesis in ("all", "H1"):
            run_h1(writer, iters, spec)
        if args.hypothesis in ("all", "H2"):
            run_h2(writer, iters, spec)
        if args.hypothesis in ("all", "H3"):
            run_h3(writer, iters, spec, h3_configs)
    except KeyboardInterrupt:
        print("\n\n  ⚠️ Interrupted! Saving partial results...")
    finally:
        writer.close()
        stop_workload()
        stop_perf_record()

    print(f"\n{'='*70}")
    print(f"  ✅ COMPLETE — Results: {args.output}")
    print(f"  Next: python3 ../common/analyze_v2.py {args.output}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
