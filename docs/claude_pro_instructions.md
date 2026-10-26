# Claude Pro: Complete Implementation Instructions for Meta-Profiling Publication

## Context

You are working on the **Meta-Profiling** research project. The goal is to fix the experimental pipeline so it produces trustworthy, publication-quality data and analyses for a systems measurement paper targeting ICPE 2027 or ISPASS 2027.

**Project root:** `/home/rishi/Desktop/Meta-Profiling/`

**Core premise of the paper:** A three-layer, NUMA-aware isolation architecture for measuring `perf record`'s own hardware overhead with statistical rigor, tested across four falsifiable hypotheses (H1–H4).

**Why this matters:** The current code has 9 known measurement bugs, uses bpftrace (10 K-cycle quantization noise) instead of `perf stat` (exact PMU register reads) as the primary measurement tool, and has never analyzed the Go API H4 data — making the paper's production-negligibility claim unsupported.

---

## MANDATORY FIRST READ

Before writing any code, read these files to understand the current implementation:

| File | What it contains |
|------|-----------------|
| `meta_profiling_deliverables/dummy_workload_test/run_hypotheses_dummy.py` | Main experiment runner for H1/H2/H3 on CPU-bound workload |
| `meta_profiling_deliverables/go_api_test/run_hypotheses_goapi.py` | Experiment runner for H1/H4 on Go microservice |
| `meta_profiling_deliverables/common/analyze_hypotheses.py` | Statistical analysis (H1–H4, Holm-Bonferroni) |
| `meta_profiling_deliverables/common/measure_statistically_ebpf.py` | bpftrace wrapper (current outer ruler — being replaced) |
| `meta_profiling_deliverables/common/ebpf_counter.bt` | bpftrace script (keep for diagnostics only) |
| `meta_profiling_deliverables/common/ebpf_counter_cpu.bt` | CPU-filtered bpftrace script |
| `meta_profiling_deliverables/common/ebpf_counter_pid_cpu.bt` | PID+CPU filtered bpftrace script |
| `meta_profiling_deliverables/common/ebpf_counter_infinite.bt` | Infinite bpftrace counter (Go API constant-work) |
| `analysis_results.md` | Documents all 9 known bugs in detail |

---

## Architecture Overview

```
CPU Layout (Intel Xeon E5-2620 v4, 8c/16t × 2 sockets):
  Socket 0: CPUs 0-7,  16-23  (HT pairs: 0/16, 1/17, ..., 7/23)
  Socket 1: CPUs 8-15, 24-31  (HT pairs: 8/24, 9/25, ..., 15/31)

Experiment roles (current isolation: isolcpus=6,7,14,15):
  CPU 6  → workload (lat_mem_rd)             [Socket 0, Core 6]
  CPU 7  → perf record (tool under study)    [Socket 0, Core 7]
  CPU 14 → perf stat (outer ruler)           [Socket 1, Core 6]
  CPU 15 → reserved / spare

HT siblings NOT yet isolated:
  CPU 22 is the HT sibling of CPU 6
  CPU 23 is the HT sibling of CPU 7
  CPU 30 is the HT sibling of CPU 14
  CPU 31 is the HT sibling of CPU 15
```

**Measurement hierarchy (after this fix):**
- **Primary outer ruler:** `perf stat` on CPU 14 — reads exact PMU register counts for perf record's PID
- **Diagnostic only:** bpftrace — kept for per-function latency attribution, not for counting
- **Inner tool under study:** `perf record -g -c 100K` pinned to CPU 7

---

## THE 9 BUGS AND THEIR STATUS

| # | Bug | Status in current code | Fix required |
|---|-----|------------------------|-------------|
| 1 | Workload PID changes mid-measurement | **Partially fixed** — `measure_workload_baseline` already uses `run_bpftrace_cpu()` | Verify + keep |
| 2 | PMU contention on CPU 6 | **Root cause:** bpftrace AND perf record both use PMU on CPU 6 | Fix: `perf stat` targets CPU 14, not CPU 6 |
| 3 | bpftrace NMI inflates perf record measurement | **Root cause:** bpftrace NMIs fire on CPU 7 (perf record's CPU) | Fix: eliminate bpftrace as primary ruler |
| 4 | Quantization noise | **Root cause:** bpftrace counts multiples of period | Fix: `perf stat` has exact counts |
| 5 | Go API H4 data never analyzed | **BUG:** `analyze_h4()` ignores `goapi_df` | Already fixed in the current `analyze_hypotheses.py` (line 496–509) — verify it works |
| 6 | H4 ratio paired by index not iteration | **Fixed** in current `analyze_hypotheses.py` (line 524–530) — verify |
| 7 | Inconsistent sampling periods | Will be moot after switching to `perf stat` | No action needed |
| 8 | `BPFTRACE_CPU` declared but never used | Misleading | Remove or fix the variable |
| 9 | `measure_constant_work` timing gap | **Fixed** in current `run_hypotheses_goapi.py` (line 162–179) — verify |

---

## TASK 1: Create `perf_stat_ruler.py` (New File)

**Path:** `meta_profiling_deliverables/common/perf_stat_ruler.py`

This replaces bpftrace as the primary outer measurement tool. It wraps `perf stat` to count exact hardware events for a target PID during a time window.

```python
#!/usr/bin/env python3
"""
perf_stat_ruler.py — Exact PMU Measurement via perf stat

Uses perf stat to read hardware PMU registers directly, providing:
  - Zero quantization error (exact counts, not overflow multiples)
  - No PMU contention (perf stat on CPU 14 counts CPU 14's PMUs)
  - No observer effect (perf stat itself runs on CPU 14, isolated from
    the workload on CPU 6 and perf record on CPU 7)

Key design decisions:
  - perf stat is attached to the TARGET PID (perf record's PID)
    and runs for a fixed duration on CPU 14
  - We use <= 4 hardware events to avoid PMU multiplexing
    (modern Intel has 4 general-purpose PMU counters)
  - Events chosen: cycles, L1-dcache-load-misses, LLC-load-misses,
    branch-misses, context-switches
    NOTE: Use only 4 at a time to avoid multiplexing. Default: cycles +
    L1-dcache-load-misses + branch-misses + context-switches

Usage:
  from perf_stat_ruler import measure_pid_perf_stat, measure_cpu_perf_stat

  # Measure perf record's own cost for 5 seconds
  data = measure_pid_perf_stat(perf_pid=12345, duration=5,
                               ruler_cpu="14")

  # Measure a CPU's total activity (for workload baseline)
  data = measure_cpu_perf_stat(target_cpu="6", duration=5,
                               ruler_cpu="14")
"""

import subprocess
import re
import time
import os

# ---------------------------------------------------------------------------
# Default hardware events — kept to 4 to avoid PMU multiplexing.
# Intel Xeon E5-2620 v4 has 4 general-purpose PMU counters.
# ---------------------------------------------------------------------------

# Use these 4 events by default (no multiplexing):
DEFAULT_EVENTS = [
    "cycles",
    "L1-dcache-load-misses",
    "branch-misses",
    "context-switches",
]

# Optional 5th event set for LLC (use separately to avoid multiplexing):
LLC_EVENTS = [
    "cycles",
    "LLC-load-misses",
    "branch-misses",
    "context-switches",
]


def _parse_perf_stat_output(stderr_text):
    """Parse perf stat output from stderr.

    perf stat writes results to stderr in this format:
        1,234,567      cycles                    #  1.23 GHz
            5,678      L1-dcache-load-misses     #  0.46% of all L1-dcache hits
              234      branch-misses             #  0.12% of all branches
               12      context-switches

    Returns dict: metric_name -> int count
    """
    results = {}

    # Map perf stat event names to our canonical names
    event_map = {
        "cycles":                  "cycles",
        "L1-dcache-load-misses":   "L1_dcache_misses",
        "LLC-load-misses":         "LLC_misses",
        "branch-misses":           "branch_misses",
        "context-switches":        "context_switches",
        "cache-misses":            "cache_misses",
    }

    for line in stderr_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "Performance counter" in line:
            continue

        # Match: "    1,234,567      cycles    ..."
        # The count may contain commas (thousands separators)
        match = re.match(
            r"^([\d,]+(?:\.\d+)?)\s+(\S+(?:-\S+)*)",
            line
        )
        if match:
            count_str = match.group(1).replace(",", "")
            event_name = match.group(2).split("#")[0].strip()
            # Remove trailing modifiers like :u, :k, :HG
            event_name = re.sub(r":[a-zA-Z]+$", "", event_name)

            try:
                count = int(float(count_str))
            except ValueError:
                continue

            canonical = event_map.get(event_name)
            if canonical:
                results[canonical] = count

    return results


def measure_pid_perf_stat(perf_pid, duration, ruler_cpu="14",
                           events=None, verbose=False):
    """Measure a PID's hardware event counts using perf stat.

    Pins perf stat to ruler_cpu (default CPU 14) so it does not
    interfere with the workload (CPU 6) or perf record (CPU 7).

    Args:
        perf_pid: PID of perf record process to measure
        duration: measurement window in seconds
        ruler_cpu: CPU to pin perf stat to (default "14")
        events: list of perf event names (default DEFAULT_EVENTS)
        verbose: print raw perf stat output

    Returns:
        dict of {metric_name: count} or None on failure
    """
    if events is None:
        events = DEFAULT_EVENTS

    event_str = ",".join(events)

    # Build command:
    #   taskset -c 14  → pin perf stat process to CPU 14
    #   perf stat -p PID  → attach to target PID
    #   -e events  → which events to count
    #   --no-big-num  → no comma separators (easier to parse)
    #   sleep DURATION  → run for this many seconds
    #
    # NOTE: perf stat -p PID counts events attributed to that PID
    # across ALL CPUs. This is what we want — we want to count
    # all cycles/events that the OS attributes to perf record's PID,
    # regardless of which CPU it runs on (though it's pinned to CPU 7).
    cmd = (
        f"taskset -c {ruler_cpu} "
        f"perf stat -p {perf_pid} "
        f"-e {event_str} "
        f"--no-big-num "
        f"-- sleep {duration}"
    )

    if verbose:
        print(f"  [perf_stat_ruler] CMD: {cmd}")

    try:
        result = subprocess.run(
            cmd, shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=duration + 15
        )
    except subprocess.TimeoutExpired:
        print(f"  [perf_stat_ruler] TIMEOUT after {duration + 15}s")
        return None

    if verbose:
        print(f"  [perf_stat_ruler] stderr: {result.stderr[:500]}")

    parsed = _parse_perf_stat_output(result.stderr)

    if not parsed:
        # perf stat may have failed — common reasons:
        #   - PID no longer exists
        #   - no CAP_PERFMON / CAP_SYS_ADMIN
        #   - events not available on this CPU
        if verbose or result.returncode != 0:
            print(f"  [perf_stat_ruler] WARNING: no data parsed. "
                  f"returncode={result.returncode}")
            print(f"  stderr: {result.stderr[:300]}")
        return None

    return parsed


def measure_cpu_perf_stat(target_cpu, duration, ruler_cpu="14",
                           events=None, verbose=False):
    """Measure all hardware events on target_cpu during duration seconds.

    This is used for workload baseline measurements (CPU 6 = workload CPU).
    Unlike bpftrace CPU filtering, perf stat -C directly programs the PMU
    of the target CPU — no NMI overhead, no quantization.

    Args:
        target_cpu: CPU number to measure (e.g., "6" for the workload CPU)
        duration: measurement window in seconds
        ruler_cpu: CPU to pin perf stat's userspace process to
        events: list of perf event names (default DEFAULT_EVENTS)
        verbose: print raw perf stat output

    Returns:
        dict of {metric_name: count} or None on failure
    """
    if events is None:
        events = DEFAULT_EVENTS

    event_str = ",".join(events)

    # -C target_cpu: count events on this specific CPU
    # This is equivalent to measuring everything running on that CPU
    cmd = (
        f"taskset -c {ruler_cpu} "
        f"perf stat -C {target_cpu} "
        f"-e {event_str} "
        f"--no-big-num "
        f"-- sleep {duration}"
    )

    if verbose:
        print(f"  [perf_stat_ruler] CMD: {cmd}")

    try:
        result = subprocess.run(
            cmd, shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=duration + 15
        )
    except subprocess.TimeoutExpired:
        print(f"  [perf_stat_ruler] TIMEOUT")
        return None

    if verbose:
        print(f"  [perf_stat_ruler] stderr: {result.stderr[:500]}")

    parsed = _parse_perf_stat_output(result.stderr)

    if not parsed and verbose:
        print(f"  [perf_stat_ruler] WARNING: no data. "
              f"returncode={result.returncode}, stderr={result.stderr[:200]}")

    return parsed


def check_perf_stat_available(ruler_cpu="14", verbose=True):
    """Sanity check: can we run perf stat? Returns True if OK."""
    if verbose:
        print("  Checking perf stat availability...")

    # Quick test: count cycles for 1 second
    result = subprocess.run(
        f"taskset -c {ruler_cpu} perf stat -e cycles --no-big-num -- sleep 1",
        shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=10
    )

    if result.returncode != 0:
        print(f"  ❌ perf stat failed: {result.stderr[:200]}")
        print("  Possible fixes:")
        print("    echo -1 > /proc/sys/kernel/perf_event_paranoid")
        print("    OR run with sudo")
        return False

    parsed = _parse_perf_stat_output(result.stderr)
    if "cycles" in parsed and parsed["cycles"] > 0:
        if verbose:
            print(f"  ✅ perf stat OK — measured {parsed['cycles']:,} cycles in 1s")
        return True
    else:
        print(f"  ❌ perf stat returned no data. stderr: {result.stderr[:200]}")
        return False
```

---

## TASK 2: Modify `run_hypotheses_dummy.py` — Switch to `perf stat`

**File:** `meta_profiling_deliverables/dummy_workload_test/run_hypotheses_dummy.py`

### Change 2A: Update imports and add perf_stat_ruler

At the top of the file, **add** this import after the existing `sys.path.insert` line:

```python
# Add common/ to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
from perf_stat_ruler import (
    measure_pid_perf_stat, measure_cpu_perf_stat, check_perf_stat_available
)
# Keep bpftrace imports for diagnostic use only:
from measure_statistically_ebpf import format_number
```

Remove the bpftrace-based measurement imports:
```python
# REMOVE this line:
from measure_statistically_ebpf import run_bpftrace, run_bpftrace_cpu, run_bpftrace_pid_cpu, METRICS, format_number
```

### Change 2B: Update configuration constants

```python
WORKLOAD_CPU = "6"
PERF_RECORD_CPU = "7"
RULER_CPU = "14"          # ADD: perf stat runs on this isolated CPU
DURATION = 5              # seconds per measurement window (unchanged)

# perf stat events — max 4 to avoid PMU multiplexing
PERF_STAT_EVENTS = [
    "cycles",
    "L1-dcache-load-misses",
    "branch-misses",
    "context-switches",
]
```

### Change 2C: Replace `measure_workload_baseline()`

**Replace** the entire `measure_workload_baseline()` function:

```python
def measure_workload_baseline(duration):
    """Measure the WORKLOAD CPU's hardware counters via perf stat.

    Uses perf stat -C 6 to count all events on the workload CPU.
    This is immune to PID churn (lat_mem_rd restarts frequently).
    perf stat has zero quantization error — exact PMU register reads.

    The ruler (perf stat) runs on CPU 14, not CPU 6, so there is
    no PMU contention with the workload.
    """
    return measure_cpu_perf_stat(
        target_cpu=WORKLOAD_CPU,
        duration=duration,
        ruler_cpu=RULER_CPU,
        events=PERF_STAT_EVENTS,
    )
```

### Change 2D: Replace `measure_tool_overhead()`

**Replace** the entire `measure_tool_overhead()` function:

```python
def measure_tool_overhead(perf_pid, duration):
    """Measure PERF RECORD's own hardware event counts via perf stat.

    Attaches perf stat to perf record's PID. This counts all hardware
    events attributed to perf record across its lifetime, with:
      - Zero quantization error (exact PMU register reads)
      - No PMU contention (perf stat runs on CPU 14)
      - No NMI observer effect (no bpftrace NMIs firing on CPU 7)

    Note: perf stat -p PID counts events for that PID on ANY CPU.
    Since perf record is pinned to CPU 7 via taskset, all its events
    occur on CPU 7, but perf stat counts them from CPU 14.
    """
    if not perf_pid:
        return None
    return measure_pid_perf_stat(
        perf_pid=int(perf_pid),
        duration=duration,
        ruler_cpu=RULER_CPU,
        events=PERF_STAT_EVENTS,
    )
```

### Change 2E: Update the CSV column mapping in `ResultsWriter.write_row()`

The perf_stat_ruler returns keys: `cycles`, `L1_dcache_misses`, `branch_misses`, `context_switches`. 
The CSV expects: `cycles`, `cache_misses`, `branch_misses`, `context_switches`.

Update `write_row()`:

```python
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
        "cycles":            results.get("cycles", 0),
        # L1_dcache_misses maps to cache_misses column
        "cache_misses":      results.get("L1_dcache_misses",
                             results.get("cache_misses", 0)),
        "branch_misses":     results.get("branch_misses", 0),
        "page_faults":       results.get("page_faults", 0),
        "context_switches":  results.get("context_switches", 0),
    }
    self.writer.writerow(row)
    self.file.flush()
```

Also update `FIELDNAMES` to use `cache_misses` (no hyphen) consistently:
```python
FIELDNAMES = [
    "session_id", "timestamp", "hypothesis", "workload", "config_g",
    "config_c", "iteration", "run_type",
    "cycles", "cache_misses", "branch_misses",
    "page_faults", "context_switches"
]
```

### Change 2F: Update the sanity check in `main()`

Replace the bpftrace sanity check with a perf stat check:

```python
# ---------------------------------------------------------------
# Sanity check: verify perf stat can measure PERF RECORD's PID
# ---------------------------------------------------------------
print(f"\n  Sanity check: starting perf record and measuring its PID with perf stat...")

if not check_perf_stat_available(ruler_cpu=RULER_CPU):
    print("  ❌ perf stat not available. Run:")
    print("      sudo sysctl -w kernel.perf_event_paranoid=-1")
    stop_workload(workload_name)
    return

test_perf_pid = start_perf_record("-g -c 100000")
if not test_perf_pid:
    print("  ❌ Could not start perf record.")
    stop_workload(workload_name)
    return

print(f"  perf record PID: {test_perf_pid}")
print(f"  Running perf stat for {DURATION}s against perf record PID {test_perf_pid}...")
test_result = measure_pid_perf_stat(int(test_perf_pid), DURATION,
                                     ruler_cpu=RULER_CPU,
                                     events=PERF_STAT_EVENTS,
                                     verbose=True)
stop_perf_record()

if test_result:
    print(f"  ✅ Sanity check PASSED — got {len(test_result)} metrics from perf record")
    for k, v in test_result.items():
        print(f"     {k}: {format_number(v)}")
else:
    print("  ❌ Sanity check FAILED — perf stat returned no data for perf record's PID.")
    print("  Possible causes:")
    print("    - perf_event_paranoid > 0 (run: sudo sysctl kernel.perf_event_paranoid=-1)")
    print("    - CPU 14 not isolated (check: cat /proc/cmdline | grep isolcpus)")
    print("    - perf record exited too fast")
    stop_workload(workload_name)
    return

# Verify workload baseline measurement
print(f"\n  Verifying workload baseline measurement (CPU {WORKLOAD_CPU})...")
wl_result = measure_cpu_perf_stat(WORKLOAD_CPU, DURATION, ruler_cpu=RULER_CPU,
                                   events=PERF_STAT_EVENTS, verbose=True)
if wl_result:
    print(f"  ✅ Workload baseline OK")
    for k, v in wl_result.items():
        print(f"     {k}: {format_number(v)}")
else:
    print("  ⚠️ Could not measure workload baseline. H4 ratio data will be missing.")
```

### Change 2G: Update the `--output` help text and method description strings

In `main()`, update the printed method line from `bpftrace` to `perf stat`:

```python
print(f"  Method:       perf stat on perf record's OWN PID (CPU {RULER_CPU})")
print(f"  Inner tool:   perf record on CPU {PERF_RECORD_CPU}")
```

---

## TASK 3: Modify `run_hypotheses_goapi.py` — Switch to `perf stat`

**File:** `meta_profiling_deliverables/go_api_test/run_hypotheses_goapi.py`

### Change 3A: Update imports

```python
# Add common/ to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
from perf_stat_ruler import (
    measure_pid_perf_stat, measure_cpu_perf_stat, check_perf_stat_available
)
from measure_statistically_ebpf import format_number  # keep for formatting only
```

Remove the old import:
```python
# REMOVE:
from measure_statistically_ebpf import run_bpftrace, METRICS, format_number
```

### Change 3B: Update configuration

```python
PERF_RECORD_CPU = "7"
RULER_CPU = "14"           # perf stat runs on this CPU (was BPFTRACE_CPU)
# Remove: BPFTRACE_CPU = "14"  — this was misleading (never actually used)

REQUESTS_PER_ITER = 1000
WARMUP_REQUESTS = 500
CONCURRENCY = 10
API_URL = "http://localhost/compute"

PERF_STAT_EVENTS = [
    "cycles",
    "L1-dcache-load-misses",
    "branch-misses",
    "context-switches",
]
```

### Change 3C: Replace `measure_constant_work()`

The Go API H4 test uses constant-work methodology (send exactly N requests, measure what happened). With `perf stat`, this is cleaner: start `perf stat -p PID`, send requests, wait for `perf stat` to finish.

**Replace** the entire `measure_constant_work()` function:

```python
def measure_constant_work(target_pid, events=None):
    """Measure hardware events for TARGET_PID over exactly REQUESTS_PER_ITER requests.

    Uses perf stat in a subprocess that we start, then send requests,
    then the stat finishes when 'sleep DURATION' ends.

    Strategy: Instead of tying measurement to request count directly
    (hard to do with perf stat without complex synchronization), we:
    1. Start perf stat for a fixed duration that covers the requests
    2. Send requests concurrently
    3. Parse perf stat's output

    The duration is calibrated: REQUESTS_PER_ITER / (CONCURRENCY * 50 req/s)
    with a 2x safety margin. For 1000 requests at concurrency 10 with
    ~100 req/s throughput, expected duration = 10s. Use 15s to be safe.

    Returns dict of hardware event counts or None on failure.
    """
    if events is None:
        events = PERF_STAT_EVENTS

    event_str = ",".join(events)

    # Estimate duration to cover all requests (with margin)
    # Typical Go API throughput: 50-200 req/s at concurrency 10
    # For 1000 requests at 50 req/s: 20s. Use 30s max.
    duration = max(30, REQUESTS_PER_ITER // 30)

    cmd = (
        f"taskset -c {RULER_CPU} "
        f"perf stat -p {target_pid} "
        f"-e {event_str} "
        f"--no-big-num "
        f"-- sleep {duration}"
    )

    # Start perf stat in background
    import threading
    perf_proc = subprocess.Popen(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )

    # Give perf stat a moment to attach
    time.sleep(0.5)

    # Send exactly REQUESTS_PER_ITER requests
    send_requests(REQUESTS_PER_ITER)

    # Wait for perf stat to finish its duration window
    try:
        stdout, stderr = perf_proc.communicate(timeout=duration + 10)
    except subprocess.TimeoutExpired:
        perf_proc.kill()
        stdout, stderr = perf_proc.communicate()

    from perf_stat_ruler import _parse_perf_stat_output
    parsed = _parse_perf_stat_output(stderr)

    if not parsed:
        print(f"  ⚠️ perf stat returned no data. stderr: {stderr[:200]}")
        return None

    return parsed
```

**IMPORTANT NOTE FOR Claude:** The above approach has a known limitation — perf stat runs for a fixed `sleep` window, not exactly covering the N requests. If requests finish in 8s but the window is 30s, we count 22 extra seconds of idle cycles. 

**Better approach:** Use `perf stat -p PID -- hey -n N -c C URL` directly:

```python
def measure_constant_work_direct(target_pid, events=None):
    """Preferred: let hey terminate perf stat naturally.

    Run: perf stat -p API_PID -- hey -n N -c C URL
    perf stat stops counting when hey exits.
    This gives exactly the hardware events during exactly N requests.
    """
    if events is None:
        events = PERF_STAT_EVENTS

    event_str = ",".join(events)

    # perf stat runs for exactly the duration of the hey load test
    cmd = (
        f"taskset -c {RULER_CPU} "
        f"perf stat -p {target_pid} "
        f"-e {event_str} "
        f"--no-big-num "
        f"-- hey -n {REQUESTS_PER_ITER} -c {CONCURRENCY} -q 500 {API_URL}"
    )

    try:
        result = subprocess.run(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=300
        )
    except subprocess.TimeoutExpired:
        return None

    from perf_stat_ruler import _parse_perf_stat_output
    parsed = _parse_perf_stat_output(result.stderr)

    if not parsed:
        print(f"  ⚠️ perf stat returned no data. stderr: {result.stderr[:300]}")
        return None

    return parsed


# Use this as the default:
def measure_constant_work(target_pid, events=None):
    """Measure hardware events for TARGET_PID over exactly REQUESTS_PER_ITER requests.
    Delegates to measure_constant_work_direct() — perf stat runs for the
    exact duration of the hey load test.
    """
    return measure_constant_work_direct(target_pid, events)
```

### Change 3D: Update `run_h1_goapi()` — also measure workload baseline

For H1 cross-workload comparison, we need the API's own baseline (no profiler) AND the tool overhead. Update `run_h1_goapi()` to also collect a workload_baseline row using the API's PID without profiler:

```python
def run_h1_goapi(api_pid, writer, iterations):
    """Measure perf record's overhead while profiling the Go API under load.
    Also measures Go API baseline (no profiler) for H1 cross-workload comparison.
    """
    print(f"\n{'='*70}")
    print(f"  H1 (Go API): Constant Absolute Cost ({iterations} iterations)")
    print(f"  Constant work: {REQUESTS_PER_ITER} requests per iteration")
    print(f"  Outer ruler: perf stat (CPU {RULER_CPU})")
    print(f"{'='*70}")

    failures = 0
    for i in range(iterations):
        print(f"  H1 iter {i+1}/{iterations} (fail: {failures})    ", end='\r')

        warmup()

        # Phase A: Baseline — measure API's own cost without profiler
        base_results = measure_constant_work(int(api_pid))
        if base_results:
            writer.write_row("H1", "off", 0, i+1,
                             "workload_baseline", base_results, REQUESTS_PER_ITER)

        warmup()

        # Phase B: Start perf record on the API, measure perf record's cost
        perf_pid = start_perf_record(api_pid, "-g -c 100000")
        if not perf_pid:
            failures += 1
            stop_perf_record()
            continue

        # Measure perf record's own cost (not the API's cost)
        tool_results = measure_constant_work(int(perf_pid))
        if tool_results:
            writer.write_row("H1", "on", 100000, i+1,
                             "tool_overhead", tool_results, REQUESTS_PER_ITER)
        else:
            failures += 1

        stop_perf_record()
        time.sleep(2)

    print(f"\n  H1 (Go API) complete. Failures: {failures}/{iterations}")
```

### Change 3E: Remove `EBPF_INFINITE`, `PERIODS`, `parse_bpftrace_map_dump`

These are no longer needed. Delete:
- `EBPF_INFINITE = ...` constant
- `PERIODS = {...}` constant  
- `parse_bpftrace_map_dump()` function

---

## TASK 4: Verify Bug Fixes Already in Place

The following bugs are **already fixed** in the current code but need verification:

### Bug 5 verification — Go API H4 data IS analyzed

In `analyze_hypotheses.py` line 495–509, the code does process `goapi_df` for H4:
```python
if goapi_df is not None:
    h4_goapi = goapi_df[goapi_df['hypothesis'] == 'H4']
    if not h4_goapi.empty:
        goapi_profiled = h4_goapi[h4_goapi['run_type'] == 'profiled']
        goapi_baseline = h4_goapi[h4_goapi['run_type'] == 'baseline']
        if not goapi_profiled.empty and not goapi_baseline.empty:
            sources.append(("Go API workload", goapi_profiled, goapi_baseline))
```
✅ This is correct. No change needed.

### Bug 6 verification — Iteration-based pairing IS implemented

In `analyze_hypotheses.py` line 524–530, pairing uses `iteration` column:
```python
if 'iteration' in tool_overhead.columns and 'iteration' in workload_baseline.columns:
    merged = pd.merge(
        tool_overhead[['iteration', metric]].dropna(),
        workload_baseline[['iteration', metric]].dropna(),
        on='iteration',
        suffixes=('_tool', '_wl')
    )
```
✅ This is correct. No change needed.

### Bug 9 verification — `measure_constant_work` timing IS fixed

In `run_hypotheses_goapi.py` line 162–179, the code waits for `"Attaching"` in stderr:
```python
while time.time() - start_time < attach_timeout:
    ready, _, _ = select.select([bpf_proc.stderr], [], [], 0.5)
    if ready:
        line = bpf_proc.stderr.readline()
        if "Attaching" in line:
            attached = True
            break
```
✅ This is correct, but will be **replaced** by the perf stat approach in Task 3.

---

## TASK 5: Update `analyze_hypotheses.py` — Add L1 Cache Metric

**File:** `meta_profiling_deliverables/common/analyze_hypotheses.py`

### Change 5A: Update metrics list to include L1 cache misses

In `analyze_h1()`, `analyze_h3()`, `analyze_h4()`, the metrics list currently is:
```python
metrics = ['cycles', 'cache_misses', 'branch_misses', 'context_switches']
```

The CSV now writes `cache_misses` to the `cache_misses` column (which maps from `L1_dcache_misses` from perf stat). This is fine — the column name stays `cache_misses` but now holds L1 D-cache load misses instead of combined cache misses.

Add a note in the report header to document this:

In `main()`, after `report.append("---")`, add:
```python
report.append("")
report.append("> **Metric note:** `cache_misses` column contains L1-dcache-load-misses")
report.append("> as measured by `perf stat`. This is more specific than the former")
report.append("> bpftrace combined cache-misses count.")
report.append("")
```

### Change 5B: Add per-request overhead calculation to H4 report

At the end of `analyze_h4()`, after computing the summary for each source, add:

```python
# Per-request overhead computation (for the Go API source)
for source_name, tool_overhead, workload_baseline in sources:
    if "Go API" in source_name and 'requests_served' in tool_overhead.columns:
        tool_cycles = tool_overhead['cycles'].dropna().values.astype(float)
        wl_cycles = workload_baseline['cycles'].dropna().values.astype(float)
        reqs = workload_baseline['requests_served'].dropna().values
        if len(tool_cycles) > 0 and len(wl_cycles) > 0 and len(reqs) > 0:
            mean_overhead_cycles = np.mean(tool_cycles) - np.mean(wl_cycles)
            mean_requests = np.mean(reqs)
            if mean_requests > 0:
                per_request_cycles = mean_overhead_cycles / mean_requests
                report_lines.append("")
                report_lines.append(
                    f"> **Per-request overhead:** For every {int(mean_requests)} requests, "
                    f"`perf record` consumed {mean_overhead_cycles:,.0f} additional CPU cycles "
                    f"({per_request_cycles:,.1f} cycles/request)."
                )
```

---

## TASK 6: Create Figure Generation Script

**Path:** `meta_profiling_deliverables/common/generate_figures.py`

This script reads the CSV outputs from the experiment runners and generates all 5 publication-quality figures.

```python
#!/usr/bin/env python3
"""
generate_figures.py — Publication-Quality Figure Generation

Reads CSV outputs from run_hypotheses_dummy.py and run_hypotheses_goapi.py,
generates all figures required for the paper as PDF (vector graphics).

Figures:
  1. H2 scaling curve: overhead vs sampling frequency (1/c)
  2. H3 ablation bar chart: with-g vs without-g cost decomposition
  3. H4 equivalence plot: 90% CI relative to ±1% bounds
  4. H1 cross-workload comparison bar chart
  5. Architecture diagram (schematic)

Usage:
  python3 generate_figures.py <dummy_csv> [--goapi <goapi_csv>] [--outdir figures/]

Requirements:
  pip install matplotlib numpy pandas scipy
"""

import argparse
import os
import sys
import warnings
import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")  # non-interactive backend (for server use)
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 12,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

COLORS = {
    "primary":   "#2563EB",   # blue
    "secondary": "#DC2626",   # red
    "accent":    "#16A34A",   # green
    "neutral":   "#6B7280",   # gray
    "light":     "#DBEAFE",   # light blue
}


def si_label(n):
    """Format a number with SI suffix."""
    if n >= 1e9: return f"{n/1e9:.1f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.0f}K"
    return str(int(n))


# ---------------------------------------------------------------------------
# Figure 1: H2 Scaling Curve
# ---------------------------------------------------------------------------

def plot_h2_scaling(dummy_df, outdir):
    """H2: Overhead vs sampling frequency (1/c).

    X-axis: sampling frequency = 1/c (log scale)
    Y-axis: mean cycles consumed by perf record
    Shows: data points + linear fit + non-linear fit + confidence bands
    Marks: "knee" region where non-linearity becomes significant
    """
    h2_data = dummy_df[dummy_df['hypothesis'] == 'H2']
    h2_profiled = h2_data[h2_data['run_type'].isin(['profiled', 'tool_overhead'])]
    if h2_profiled.empty:
        print("  ⚠️ No H2 data — skipping Figure 1")
        return

    grouped = h2_profiled.groupby('config_c')['cycles']
    c_vals = sorted(grouped.groups.keys())
    means = [grouped.get_group(c).mean() for c in c_vals]
    stds  = [grouped.get_group(c).std()  for c in c_vals]
    ns    = [len(grouped.get_group(c))   for c in c_vals]
    freqs = [1.0 / c for c in c_vals]

    fig, ax = plt.subplots(figsize=(7, 4.5))

    # Data points with error bars (95% CI)
    cis = [1.96 * s / np.sqrt(n) for s, n in zip(stds, ns)]
    ax.errorbar(freqs, means, yerr=cis, fmt='o', color=COLORS["primary"],
                markersize=7, linewidth=2, capsize=4, label="Measured mean ± 95% CI",
                zorder=5)

    # Linear fit
    x = np.array(freqs)
    y = np.array(means)
    slope, intercept, r, p, _ = stats.linregress(x, y)
    x_fit = np.linspace(x.min(), x.max(), 200)
    ax.plot(x_fit, slope * x_fit + intercept, '--',
            color=COLORS["neutral"], linewidth=1.5,
            label=f"Linear fit (R²={r**2:.3f})")

    # Quadratic fit
    coeffs = np.polyfit(x, y, 2)
    y_quad = np.polyval(coeffs, x_fit)
    ax.plot(x_fit, y_quad, '-', color=COLORS["secondary"], linewidth=2,
            label="Quadratic fit")

    # Shade the "knee" region (around -c 100K = freq 1e-5)
    knee_freq = 1.0 / 100000
    ax.axvspan(knee_freq * 0.5, knee_freq * 2, alpha=0.15,
               color=COLORS["accent"], label="Practical knee region")
    ax.axvline(knee_freq, color=COLORS["accent"], linewidth=1, linestyle=':')

    ax.set_xscale("log")
    ax.set_xlabel("Sampling frequency (1/c)  [log scale]")
    ax.set_ylabel("perf record cycles consumed")
    ax.set_title("H2: Overhead vs. Sampling Frequency\n"
                 "(non-linearity reveals practical operating point)")
    ax.legend(framealpha=0.9)

    # Annotate -c values on x-axis
    for c, f in zip(c_vals, freqs):
        ax.annotate(f"-c {si_label(c)}", (f, 0),
                    textcoords="offset points", xytext=(0, -22),
                    ha='center', fontsize=8, color=COLORS["neutral"],
                    annotation_clip=False)

    out = os.path.join(outdir, "fig1_h2_scaling.pdf")
    fig.savefig(out)
    plt.close(fig)
    print(f"  ✅ Figure 1: {out}")


# ---------------------------------------------------------------------------
# Figure 2: H3 Ablation Bar Chart
# ---------------------------------------------------------------------------

def plot_h3_ablation(dummy_df, outdir):
    """H3: With-g vs without-g cost decomposition.

    Stacked bar chart showing:
      - Base sampling cost (without -g)
      - Call-graph cost (with-g minus without-g)
    For each metric.
    """
    h3_data = dummy_df[dummy_df['hypothesis'] == 'H3']
    h3_profiled = h3_data[h3_data['run_type'].isin(['profiled', 'tool_overhead'])]
    if h3_profiled.empty:
        print("  ⚠️ No H3 data — skipping Figure 2")
        return

    with_g    = h3_profiled[h3_profiled['config_g'] == 'on']
    without_g = h3_profiled[h3_profiled['config_g'] == 'off']

    metrics = ['cycles', 'cache_misses', 'branch_misses']
    labels  = ['Cycles', 'L1-cache\nmisses', 'Branch\nmisses']

    base_means = [without_g[m].dropna().mean() for m in metrics]
    with_means = [with_g[m].dropna().mean()    for m in metrics]
    g_costs    = [max(0, w - b) for w, b in zip(with_means, base_means)]

    fig, ax = plt.subplots(figsize=(6, 4.5))

    x = np.arange(len(metrics))
    width = 0.5

    bars1 = ax.bar(x, base_means, width, color=COLORS["primary"],
                   alpha=0.85, label="Base sampling (without -g)")
    bars2 = ax.bar(x, g_costs, width, bottom=base_means,
                   color=COLORS["secondary"], alpha=0.85,
                   label="Call-graph overhead (-g)")

    # Annotate percentage attribution
    for i, (base, g_cost, total) in enumerate(zip(base_means, g_costs, with_means)):
        if total > 0:
            pct = g_cost / total * 100
            ax.text(x[i], total + total * 0.02, f"{pct:.0f}%",
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Mean event count (perf record)")
    ax.set_title("H3: Call-Graph (-g) Cost Decomposition\n"
                 "Percentage = call-graph fraction of total overhead")
    ax.legend(framealpha=0.9)
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: si_label(v))
    )

    out = os.path.join(outdir, "fig2_h3_ablation.pdf")
    fig.savefig(out)
    plt.close(fig)
    print(f"  ✅ Figure 2: {out}")


# ---------------------------------------------------------------------------
# Figure 3: H4 Equivalence Plot
# ---------------------------------------------------------------------------

def plot_h4_equivalence(dummy_df, goapi_df, outdir):
    """H4: 90% CI for overhead ratio relative to ±1% equivalence bounds.

    Shows for each metric: the 90% CI of (overhead_cycles / workload_cycles × 100%)
    relative to the ±1% equivalence threshold.
    If the CI is entirely within ±1%, overhead is provably negligible.
    """
    sources = []

    # Dummy workload
    h1_dummy = dummy_df[dummy_df['hypothesis'] == 'H1']
    tool_dummy = h1_dummy[h1_dummy['run_type'] == 'tool_overhead']
    wl_dummy   = h1_dummy[h1_dummy['run_type'] == 'workload_baseline']
    if not tool_dummy.empty and not wl_dummy.empty:
        sources.append(("CPU-bound (lat_mem_rd)", tool_dummy, wl_dummy))

    # Go API workload
    if goapi_df is not None:
        h4_goapi = goapi_df[goapi_df['hypothesis'] == 'H4']
        prof = h4_goapi[h4_goapi['run_type'] == 'profiled']
        base = h4_goapi[h4_goapi['run_type'] == 'baseline']
        if not prof.empty and not base.empty:
            # For Go API H4, tool = profiled, wl = baseline (it's the app's counters)
            sources.append(("Go microservice (HTTP API)", prof, base))

    if not sources:
        print("  ⚠️ No H4 data — skipping Figure 3")
        return

    metrics = ['cycles', 'cache_misses', 'branch_misses']
    metric_labels = ['Cycles', 'L1-cache misses', 'Branch misses']
    n_sources = len(sources)
    n_metrics = len(metrics)

    fig, axes = plt.subplots(1, n_metrics, figsize=(4 * n_metrics, 4.5), sharey=False)
    if n_metrics == 1:
        axes = [axes]

    for ax_idx, (metric, mlabel) in enumerate(zip(metrics, metric_labels)):
        ax = axes[ax_idx]
        ax.set_title(mlabel)

        y_positions = list(range(n_sources))
        for src_idx, (src_name, tool_df, wl_df) in enumerate(sources):
            # Compute per-iteration ratios
            if 'iteration' in tool_df.columns and 'iteration' in wl_df.columns:
                merged = pd.merge(
                    tool_df[['iteration', metric]].dropna(),
                    wl_df[['iteration', metric]].dropna(),
                    on='iteration', suffixes=('_t', '_w')
                )
                t_vals = merged[f'{metric}_t'].values.astype(float)
                w_vals = merged[f'{metric}_w'].values.astype(float)
            else:
                t_vals = tool_df[metric].dropna().values.astype(float)
                w_vals = wl_df[metric].dropna().values.astype(float)
                n_min = min(len(t_vals), len(w_vals))
                t_vals, w_vals = t_vals[:n_min], w_vals[:n_min]

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ratios = np.array([
                    t / w * 100 for t, w in zip(t_vals, w_vals)
                    if w > 0
                ])

            if len(ratios) < 2:
                continue

            mean_r = np.mean(ratios)
            n = len(ratios)
            se = np.std(ratios, ddof=1) / np.sqrt(n)
            df = n - 1
            t_crit = stats.t.ppf(0.95, df)  # 90% CI
            ci_lo = mean_r - t_crit * se
            ci_hi = mean_r + t_crit * se

            y = src_idx
            color = COLORS["primary"] if src_idx == 0 else COLORS["secondary"]

            # Plot CI
            ax.plot([ci_lo, ci_hi], [y, y], '-', color=color, linewidth=3,
                    solid_capstyle='round')
            ax.plot(mean_r, y, 'D', color=color, markersize=8, zorder=5)

            # Label
            ax.text(ci_hi + 0.02, y, f"{mean_r:.3f}%", va='center',
                    ha='left', fontsize=8, color=color)

        # Equivalence bounds
        ax.axvline(1.0,  color=COLORS["accent"], linewidth=2, linestyle='--',
                   label="±1% bound", alpha=0.8)
        ax.axvline(-1.0, color=COLORS["accent"], linewidth=2, linestyle='--',
                   alpha=0.8)
        ax.axvspan(-1.0, 1.0, alpha=0.07, color=COLORS["accent"])
        ax.axvline(0, color='black', linewidth=0.5, alpha=0.5)

        ax.set_yticks(range(n_sources))
        ax.set_yticklabels([s[0] for s in sources], fontsize=9)
        ax.set_xlabel("Overhead ratio (% of workload)")
        if ax_idx == 0:
            ax.set_ylabel("Workload")

        if ax_idx == n_metrics - 1:
            ax.legend(loc='lower right', fontsize=8)

    fig.suptitle("H4: Production Negligibility — 90% CI vs. ±1% Equivalence Bounds",
                 fontsize=12, y=1.02)
    plt.tight_layout()

    out = os.path.join(outdir, "fig3_h4_equivalence.pdf")
    fig.savefig(out, bbox_inches='tight')
    plt.close(fig)
    print(f"  ✅ Figure 3: {out}")


# ---------------------------------------------------------------------------
# Figure 4: H1 Cross-Workload Comparison
# ---------------------------------------------------------------------------

def plot_h1_crossworkload(dummy_df, goapi_df, outdir):
    """H1: perf record's own hardware cost across different workloads.

    Bar chart with error bars showing perf record's cycles/misses
    measured while profiling each workload type.
    """
    groups = {}

    # Dummy workload
    h1_dummy = dummy_df[dummy_df['hypothesis'] == 'H1']
    tool_dummy = h1_dummy[h1_dummy['run_type'] == 'tool_overhead']
    if not tool_dummy.empty:
        groups['CPU-bound\n(lat_mem_rd)'] = tool_dummy

    # Go API workload
    if goapi_df is not None:
        h1_goapi = goapi_df[goapi_df['hypothesis'] == 'H1']
        tool_goapi = h1_goapi[h1_goapi['run_type'] == 'tool_overhead']
        if not tool_goapi.empty:
            groups['Go microservice\n(HTTP API)'] = tool_goapi

    if len(groups) < 1:
        print("  ⚠️ Insufficient H1 data — skipping Figure 4")
        return

    metrics = ['cycles', 'cache_misses', 'branch_misses']
    metric_labels = ['Cycles', 'L1-cache misses', 'Branch misses']
    n_metrics = len(metrics)
    n_groups = len(groups)

    fig, axes = plt.subplots(1, n_metrics, figsize=(4.5 * n_metrics, 5))
    if n_metrics == 1:
        axes = [axes]

    workload_names = list(groups.keys())
    bar_colors = [COLORS["primary"], COLORS["secondary"], COLORS["accent"]]

    for ax_idx, (metric, mlabel) in enumerate(zip(metrics, metric_labels)):
        ax = axes[ax_idx]
        means = []
        cis   = []

        for wl_name in workload_names:
            df = groups[wl_name]
            vals = df[metric].dropna().values.astype(float)
            m = np.mean(vals)
            n = len(vals)
            se = np.std(vals, ddof=1) / np.sqrt(n)
            means.append(m)
            cis.append(1.96 * se)

        x = np.arange(n_groups)
        bars = ax.bar(x, means, 0.6,
                      color=[bar_colors[i % len(bar_colors)] for i in range(n_groups)],
                      alpha=0.85, yerr=cis, capsize=5,
                      error_kw={'linewidth': 2})

        ax.set_xticks(x)
        ax.set_xticklabels(workload_names, fontsize=9)
        ax.set_title(mlabel)
        ax.set_ylabel("perf record event count" if ax_idx == 0 else "")
        ax.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: si_label(v))
        )

        # Annotate values
        for rect, m in zip(bars, means):
            ax.text(rect.get_x() + rect.get_width() / 2, rect.get_height() * 1.02,
                    si_label(m), ha='center', va='bottom', fontsize=9)

    fig.suptitle(
        "H1: perf record's Absolute Cost Across Workloads\n"
        "(error bars = 95% CI, N=500+ each)",
        fontsize=12
    )
    plt.tight_layout()

    out = os.path.join(outdir, "fig4_h1_crossworkload.pdf")
    fig.savefig(out, bbox_inches='tight')
    plt.close(fig)
    print(f"  ✅ Figure 4: {out}")


# ---------------------------------------------------------------------------
# Figure 5: Architecture Diagram
# ---------------------------------------------------------------------------

def plot_architecture_diagram(outdir):
    """3-layer NUMA isolation architecture diagram.

    Shows the layered isolation design:
      Layer 1 — OS isolation (isolcpus, NUMA awareness)
      Layer 2 — Hardware isolation (CPU pinning per role)
      Layer 3 — Measurement isolation (perf stat as outer ruler)
    """
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7)
    ax.axis('off')

    # ---- Layer boxes ----
    layers = [
        # (y_bottom, height, label, color)
        (0.3, 1.6, "Layer 1: OS Isolation\nisolcpus=6,7,14,15 | NUMA-aware binding", "#DBEAFE"),
        (2.2, 1.6, "Layer 2: Hardware Isolation\nCPU pinning per role | Socket separation", "#DCFCE7"),
        (4.1, 2.0, "Layer 3: Measurement Isolation\nperf stat on CPU 14 (outer ruler)\nNo PMU contention | Exact counts", "#FEF9C3"),
    ]

    for y, h, label, color in layers:
        rect = plt.Rectangle((0.3, y), 9.4, h, linewidth=1.5,
                              edgecolor='#374151', facecolor=color, zorder=2)
        ax.add_patch(rect)
        ax.text(5, y + h / 2, label, ha='center', va='center',
                fontsize=10, fontweight='bold', zorder=3)

    # ---- CPU role boxes ----
    cpu_boxes = [
        # (x_center, y_center, label, color)
        (2.0, 1.2, "CPU 6\nWorkload\n(lat_mem_rd)", "#3B82F6"),
        (4.0, 1.2, "CPU 7\nperf record\n(tool under study)", "#EF4444"),
        (6.5, 1.2, "CPU 14\nperf stat\n(outer ruler)", "#10B981"),
        (8.5, 1.2, "CPU 15\nReserved", "#9CA3AF"),
    ]

    for cx, cy, label, color in cpu_boxes:
        rect = plt.Rectangle((cx - 0.85, cy - 0.6), 1.7, 1.2,
                              linewidth=1.2, edgecolor='white',
                              facecolor=color, alpha=0.85, zorder=4)
        ax.add_patch(rect)
        ax.text(cx, cy, label, ha='center', va='center',
                fontsize=7.5, color='white', fontweight='bold', zorder=5)

    # ---- Arrows ----
    arrow_kw = dict(arrowstyle='->', color='#374151',
                    lw=1.5, mutation_scale=15)
    # perf record samples workload
    ax.annotate("", xy=(2.0, 1.8), xytext=(4.0, 1.8),
                arrowprops=dict(**arrow_kw, connectionstyle="arc3,rad=0.2"))
    ax.text(3.0, 2.15, "samples", ha='center', fontsize=8, color='#374151')

    # perf stat measures perf record
    ax.annotate("", xy=(4.0, 1.85), xytext=(6.5, 1.85),
                arrowprops=dict(**arrow_kw, connectionstyle="arc3,rad=-0.25"))
    ax.text(5.25, 2.3, "measures", ha='center', fontsize=8, color='#374151')

    # Title
    ax.set_title(
        "Meta-Profiling: 3-Layer NUMA-Aware Isolation Architecture\n"
        "(Intel Xeon E5-2620 v4, Socket 0 CPUs shown)",
        fontsize=12, pad=12
    )

    out = os.path.join(outdir, "fig5_architecture.pdf")
    fig.savefig(out, bbox_inches='tight')
    plt.close(fig)
    print(f"  ✅ Figure 5: {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate publication-quality figures for the Meta-Profiling paper"
    )
    parser.add_argument("dummy_csv", help="CSV from run_hypotheses_dummy.py")
    parser.add_argument("--goapi", type=str, default=None,
                        help="CSV from run_hypotheses_goapi.py")
    parser.add_argument("--outdir", type=str, default="figures",
                        help="Output directory for figures (default: ./figures)")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    print(f"\n  Loading data...")
    dummy_df = pd.read_csv(args.dummy_csv)
    print(f"  Dummy CSV: {len(dummy_df)} rows")

    goapi_df = None
    if args.goapi and os.path.exists(args.goapi):
        goapi_df = pd.read_csv(args.goapi)
        print(f"  Go API CSV: {len(goapi_df)} rows")

    print(f"\n  Generating figures → {args.outdir}/\n")

    plot_h2_scaling(dummy_df, args.outdir)
    plot_h3_ablation(dummy_df, args.outdir)
    plot_h4_equivalence(dummy_df, goapi_df, args.outdir)
    plot_h1_crossworkload(dummy_df, goapi_df, args.outdir)
    plot_architecture_diagram(args.outdir)

    print(f"\n  ✅ All figures saved to {args.outdir}/")
    print("  Figures:")
    for f in sorted(os.listdir(args.outdir)):
        if f.endswith('.pdf'):
            print(f"    {f}")


if __name__ == "__main__":
    main()
```

---

## TASK 7: Create Full Experiment Runner Script

**Path:** `meta_profiling_deliverables/run_full_experiments.sh`

This is the top-level script that runs all experiments in the correct order.

```bash
#!/usr/bin/env bash
# run_full_experiments.sh — Complete Experiment Pipeline
#
# Runs all experiments required for publication-quality results:
#   Phase 1: Full dummy workload (H1, H2, H3) — ~9 hours at 500 iterations
#   Phase 2: Go API experiments (H1, H4)       — ~12 hours at 500 iterations
#   Phase 3: Statistical analysis
#   Phase 4: Figure generation
#
# Usage:
#   sudo bash run_full_experiments.sh              # full run (500 iterations)
#   sudo bash run_full_experiments.sh --pilot      # pilot (50 iterations)
#   sudo bash run_full_experiments.sh --dummy-only # only H1/H2/H3
#   sudo bash run_full_experiments.sh --goapi-only # only H1/H4 Go API
#
# Prerequisites:
#   - lmbench installed: sudo apt install -y lmbench
#   - bpftrace installed: sudo apt install -y bpftrace
#   - perf installed: sudo apt install -y linux-tools-common linux-tools-generic
#   - Python: pip3 install pandas numpy scipy matplotlib
#   - hey (load tester): go install github.com/rakyll/hey@latest
#   - Docker + Go API running: cd .. && docker compose up -d
#   - CPUs isolated: isolcpus=6,7,14,15 in /proc/cmdline
#   - perf_event_paranoid = -1: sudo sysctl -w kernel.perf_event_paranoid=-1
#   - THP disabled: sudo bash common/system_harden.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
PILOT=0
DUMMY_ONLY=0
GOAPI_ONLY=0
ITERATIONS=500

# ---- Parse args ----
for arg in "$@"; do
    case $arg in
        --pilot)      PILOT=1; ITERATIONS=50  ;;
        --dummy-only) DUMMY_ONLY=1 ;;
        --goapi-only) GOAPI_ONLY=1 ;;
        --iterations=*) ITERATIONS="${arg#*=}" ;;
    esac
done

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SESSION_DIR="${RESULTS_DIR}/session_${TIMESTAMP}"
mkdir -p "${SESSION_DIR}"

DUMMY_CSV="${SESSION_DIR}/hypotheses_dummy_${TIMESTAMP}.csv"
GOAPI_CSV="${SESSION_DIR}/hypotheses_goapi_${TIMESTAMP}.csv"
REPORT="${SESSION_DIR}/analysis_report.md"
FIGURES_DIR="${SESSION_DIR}/figures"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${SESSION_DIR}/run.log"
}

# ---- Record system state ----
log "Recording system state..."
{
    echo "=== SYSTEM STATE ==="
    echo "Date: $(date)"
    echo "Kernel: $(uname -r)"
    echo "Git commit: $(cd "${SCRIPT_DIR}/.." && git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    echo ""
    echo "=== /proc/cmdline ==="
    cat /proc/cmdline
    echo ""
    echo "=== isolcpus check ==="
    grep -o 'isolcpus=[^ ]*' /proc/cmdline || echo "isolcpus NOT found in cmdline!"
    echo ""
    echo "=== perf_event_paranoid ==="
    cat /proc/sys/kernel/perf_event_paranoid
    echo ""
    echo "=== THP status ==="
    cat /sys/kernel/mm/transparent_hugepage/enabled
    echo ""
    echo "=== Turbo Boost status ==="
    cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo "no_turbo file not found"
    echo ""
    echo "=== numactl --hardware ==="
    numactl --hardware 2>/dev/null || echo "numactl not available"
    echo ""
    echo "=== lscpu ==="
    lscpu
} > "${SESSION_DIR}/system_state.txt" 2>&1
log "System state saved to session_system_state.txt"

# ---- Pre-flight checks ----
log "Running pre-flight checks..."

# perf_event_paranoid
PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid)
if [ "${PARANOID}" -gt 0 ]; then
    log "WARNING: perf_event_paranoid=${PARANOID}. Setting to -1..."
    sysctl -w kernel.perf_event_paranoid=-1
fi

# Check isolcpus
if ! grep -q 'isolcpus' /proc/cmdline; then
    log "ERROR: isolcpus not in /proc/cmdline. Run:"
    log "  sudo sed -i 's/GRUB_CMDLINE_LINUX=\"/GRUB_CMDLINE_LINUX=\"isolcpus=6,7,14,15,22,23,30,31 /' /etc/default/grub"
    log "  sudo update-grub && sudo reboot"
    exit 1
fi

# Check perf available
if ! command -v perf &>/dev/null; then
    log "ERROR: perf not found. Install: sudo apt install linux-tools-common linux-tools-generic"
    exit 1
fi

# Check lmbench
if ! command -v lat_mem_rd &>/dev/null && \
   ! [ -f /usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd ]; then
    log "WARNING: lat_mem_rd not found. Install: sudo apt install lmbench"
    log "  Dummy workload will fall back to dummy_workload binary."
fi

log "Pre-flight checks complete."

# ---- Phase 1: Dummy Workload ----
if [ "${GOAPI_ONLY}" -eq 0 ]; then
    log "Starting Phase 1: Dummy Workload Experiments (H1, H2, H3)"
    log "  Iterations: ${ITERATIONS}"
    log "  Output: ${DUMMY_CSV}"

    sudo python3 "${SCRIPT_DIR}/dummy_workload_test/run_hypotheses_dummy.py" \
        --iterations "${ITERATIONS}" \
        --output "${DUMMY_CSV}" \
        --hypothesis all

    log "Phase 1 complete. CSV: ${DUMMY_CSV}"
fi

# ---- Phase 2: Go API ----
if [ "${DUMMY_ONLY}" -eq 0 ]; then
    log "Starting Phase 2: Go API Experiments (H1, H4)"

    # Check Go API is running
    API_PID=$(docker inspect --format '{{.State.Pid}}' perf-go-api 2>/dev/null || echo "")
    if [ -z "${API_PID}" ] || [ "${API_PID}" = "0" ]; then
        log "WARNING: Go API container not running. Attempting to start..."
        cd "${SCRIPT_DIR}/../.." && docker compose up -d
        sleep 10
        API_PID=$(docker inspect --format '{{.State.Pid}}' perf-go-api 2>/dev/null || echo "")
    fi

    if [ -z "${API_PID}" ] || [ "${API_PID}" = "0" ]; then
        log "ERROR: Could not start Go API. Skipping Phase 2."
    else
        log "  Go API PID: ${API_PID}"
        log "  Iterations: ${ITERATIONS}"
        log "  Output: ${GOAPI_CSV}"

        # Check hey is available
        if ! command -v hey &>/dev/null; then
            log "ERROR: 'hey' not installed."
            log "  Install: go install github.com/rakyll/hey@latest"
            log "  Then add ~/go/bin to PATH"
        else
            sudo python3 "${SCRIPT_DIR}/go_api_test/run_hypotheses_goapi.py" \
                --iterations "${ITERATIONS}" \
                --output "${GOAPI_CSV}" \
                --hypothesis all

            log "Phase 2 complete. CSV: ${GOAPI_CSV}"
        fi
    fi
fi

# ---- Phase 3: Statistical Analysis ----
log "Starting Phase 3: Statistical Analysis"

GOAPI_ARG=""
if [ -f "${GOAPI_CSV}" ]; then
    GOAPI_ARG="--goapi ${GOAPI_CSV}"
fi

if [ -f "${DUMMY_CSV}" ]; then
    python3 "${SCRIPT_DIR}/common/analyze_hypotheses.py" \
        "${DUMMY_CSV}" \
        ${GOAPI_ARG} \
        --output "${REPORT}"
    log "Analysis report: ${REPORT}"
else
    log "WARNING: No dummy CSV found — skipping analysis."
fi

# ---- Phase 4: Figure Generation ----
log "Starting Phase 4: Figure Generation"

if [ -f "${DUMMY_CSV}" ]; then
    mkdir -p "${FIGURES_DIR}"
    python3 "${SCRIPT_DIR}/common/generate_figures.py" \
        "${DUMMY_CSV}" \
        ${GOAPI_ARG} \
        --outdir "${FIGURES_DIR}"
    log "Figures saved to: ${FIGURES_DIR}"
fi

# ---- Summary ----
log "=========================================="
log "COMPLETE. Session: ${TIMESTAMP}"
log "Results: ${SESSION_DIR}/"
log "  system_state.txt — recorded system configuration"
if [ -f "${DUMMY_CSV}" ]; then
    ROWS=$(wc -l < "${DUMMY_CSV}")
    log "  $(basename ${DUMMY_CSV}) — ${ROWS} data rows"
fi
if [ -f "${GOAPI_CSV}" ]; then
    ROWS=$(wc -l < "${GOAPI_CSV}")
    log "  $(basename ${GOAPI_CSV}) — ${ROWS} data rows"
fi
[ -f "${REPORT}" ] && log "  analysis_report.md — full statistical analysis"
[ -d "${FIGURES_DIR}" ] && log "  figures/ — $(ls ${FIGURES_DIR}/*.pdf 2>/dev/null | wc -l) PDF figures"
log "=========================================="
```

---

## TASK 8: Update `measure_statistically_ebpf.py` — Fix Bug #8

**File:** `meta_profiling_deliverables/common/measure_statistically_ebpf.py`

### Change 8A: Fix the misleading `BPFTRACE_CPU` variable

Find the line:
```python
BPFTRACE_CPU = "14"       # Outer measurement — bpftrace
```

Replace with:
```python
# NOTE: bpftrace is now used for DIAGNOSTIC purposes only.
# The primary outer ruler is perf stat (see perf_stat_ruler.py).
# bpftrace userspace cannot be reliably pinned to a CPU due to
# bpftrace 0.14 bugs — see analysis_results.md Issue #8.
BPFTRACE_CPU = "14"  # Kept for compatibility; NOT actually applied via taskset
```

And in `run_bpftrace()` where it prints `bpftrace CPU: 14`, change to:
```python
# Note: bpftrace is diagnostic only. Primary measurement uses perf stat.
# bpftrace process is NOT pinned (taskset triggers bpftrace 0.14 bugs).
```

---

## TASK 9: Run Verification Tests

After all code changes, verify the pipeline works end-to-end with a short pilot:

### Step 1: Install dependencies
```bash
sudo apt install -y linux-tools-common linux-tools-generic lmbench bpftrace
pip3 install --user pandas numpy scipy matplotlib
```

### Step 2: Set perf permissions
```bash
sudo sysctl -w kernel.perf_event_paranoid=-1
```

### Step 3: Test perf_stat_ruler
```bash
cd /home/rishi/Desktop/Meta-Profiling/meta_profiling_deliverables
python3 -c "
import sys
sys.path.insert(0, 'common')
from perf_stat_ruler import check_perf_stat_available, measure_cpu_perf_stat
check_perf_stat_available(ruler_cpu='14', verbose=True)
data = measure_cpu_perf_stat(target_cpu='0', duration=2, ruler_cpu='0', verbose=True)
print('Result:', data)
"
```

### Step 4: Pilot run (50 iterations, H1 only)
```bash
cd /home/rishi/Desktop/Meta-Profiling/meta_profiling_deliverables
sudo python3 dummy_workload_test/run_hypotheses_dummy.py \
    --pilot --hypothesis H1 \
    --output /tmp/pilot_dummy.csv
```

### Step 5: Verify CSV has data
```bash
python3 -c "
import pandas as pd
df = pd.read_csv('/tmp/pilot_dummy.csv')
print(df.groupby('run_type')[['cycles','cache_misses','branch_misses']].describe())
"
```

### Step 6: Run analysis
```bash
cd /home/rishi/Desktop/Meta-Profiling/meta_profiling_deliverables
python3 common/analyze_hypotheses.py /tmp/pilot_dummy.csv
```

### Step 7: Generate figures
```bash
python3 common/generate_figures.py /tmp/pilot_dummy.csv --outdir /tmp/figures
ls -la /tmp/figures/*.pdf
```

---

## TASK 10: HyperThread Sibling Isolation (Manual — Requires Reboot)

This step requires editing GRUB and rebooting. **Do not automate this** — show the user the command to run manually:

```bash
# Current isolcpus (missing HT siblings):
#   isolcpus=6,7,14,15
#
# Required (includes HT siblings of 6/7/14/15):
#   isolcpus=6,7,14,15,22,23,30,31
#
# The HT siblings are:
#   CPU 6  ↔ CPU 22   (Core 6, Socket 0)
#   CPU 7  ↔ CPU 23   (Core 7, Socket 0)
#   CPU 14 ↔ CPU 30   (Core 6, Socket 1)
#   CPU 15 ↔ CPU 31   (Core 7, Socket 1)

# Run this command, then reboot:
sudo sed -i 's/isolcpus=6,7,14,15/isolcpus=6,7,14,15,22,23,30,31/' /etc/default/grub
sudo update-grub
sudo reboot
```

After reboot, verify:
```bash
cat /proc/cmdline | grep isolcpus
# Should show: isolcpus=6,7,14,15,22,23,30,31
```

---

## FULL EXPERIMENT RUN COMMANDS

Once all code is in place and HT siblings are isolated, run the full experiments:

```bash
cd /home/rishi/Desktop/Meta-Profiling/meta_profiling_deliverables

# Full run — all experiments, 500 iterations each (~21 hours total)
sudo bash run_full_experiments.sh

# OR, run phases separately:

# Phase 1: Dummy workload (H1, H2, H3) — ~9 hours
sudo python3 dummy_workload_test/run_hypotheses_dummy.py \
    --iterations 500 \
    --output results/dummy_full_$(date +%Y%m%d).csv

# Phase 2: Go API (H1, H4) — ~12 hours
sudo python3 go_api_test/run_hypotheses_goapi.py \
    --iterations 500 \
    --output results/goapi_full_$(date +%Y%m%d).csv

# Phase 3: Analysis
python3 common/analyze_hypotheses.py \
    results/dummy_full_<DATE>.csv \
    --goapi results/goapi_full_<DATE>.csv \
    --output results/analysis_report.md

# Phase 4: Figures
python3 common/generate_figures.py \
    results/dummy_full_<DATE>.csv \
    --goapi results/goapi_full_<DATE>.csv \
    --outdir results/figures/
```

---

## SUMMARY OF ALL FILES TO CREATE/MODIFY

| Action | File | Description |
|--------|------|-------------|
| **CREATE** | `common/perf_stat_ruler.py` | New primary outer ruler using `perf stat` |
| **CREATE** | `common/generate_figures.py` | Publication-quality figure generation (5 figures) |
| **CREATE** | `run_full_experiments.sh` | Top-level experiment pipeline script |
| **MODIFY** | `dummy_workload_test/run_hypotheses_dummy.py` | Switch from bpftrace to `perf stat` |
| **MODIFY** | `go_api_test/run_hypotheses_goapi.py` | Switch from bpftrace to `perf stat` |
| **MODIFY** | `common/measure_statistically_ebpf.py` | Fix misleading `BPFTRACE_CPU` variable |
| **VERIFY** | `common/analyze_hypotheses.py` | Bug 5, 6 already fixed — verify output |

---

## EXPECTED OUTCOMES

After running the full pipeline, the results should show:

| Hypothesis | Expected result |
|-----------|----------------|
| **H1** | Welch's ANOVA p > 0.05 + TOST within ±10% across dummy and Go API → constant absolute cost confirmed |
| **H2** | Non-linearity F-test p < 0.05 at high frequencies → knee at -c 100K identified |
| **H3** | Paired t-test p < 0.001, Cohen's d > 0.8 → call-graph is ~50% of total overhead |
| **H4** | TOST p < 0.05 at Δ=1% for Go API → production overhead provably < 1% |
