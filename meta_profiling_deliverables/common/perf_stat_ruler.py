#!/usr/bin/env python3
"""
perf_stat_ruler.py — Exact PMU Measurement via perf stat (primary outer ruler)

Replaces bpftrace as the outer measurement tool. perf stat reads the PMU
counter registers directly, so:
  - Zero quantization error (exact counts, not multiples of an overflow period)
  - No NMIs from the ruler (counting mode never samples)
  - No PMU sharing with perf record:
      * perf stat -p <perf_record_pid> opens per-thread counters that are
        only scheduled while perf record runs — i.e. on CPU 7's PMU.
        perf record's own sampling counter lives on CPU 6's PMU (-C 6).
      * perf stat -C 6 (workload baseline) is only used while perf record
        is NOT running.
  - perf stat's userspace process is pinned to RULER_CPU (CPU 14). It is
    idle while counting; the counters are kernel-managed.

Output is requested in CSV mode (-x,) so each line carries the counter's
running percentage. If the kernel had to multiplex a counter (< 100%
running), the count is an extrapolation, not an exact read — by default
such measurements are rejected (returns None) so they never enter the CSV.

Usage:
  from perf_stat_ruler import measure_pid_perf_stat, measure_cpu_perf_stat

  # perf record's own cost for 5 seconds
  data = measure_pid_perf_stat(perf_pid=12345, duration=5, ruler_cpu="14")

  # Everything on the workload CPU for 5 seconds (PID-churn immune)
  data = measure_cpu_perf_stat(target_cpu="6", duration=5, ruler_cpu="14")

  # A PID's cost for exactly the lifetime of a command (constant work)
  data = measure_pid_during_command(api_pid, "hey -n 1000 ...", ruler_cpu="14")
"""

import subprocess

# ---------------------------------------------------------------------------
# Default events.
# Intel Xeon E5-2620 v4 (Broadwell, HT on) has 4 general-purpose counters
# per logical CPU plus a fixed cycles counter. We use 3 hardware events, so
# there is no multiplexing. context-switches and page-faults are SOFTWARE
# events — they use no PMU counter at all.
# ---------------------------------------------------------------------------

DEFAULT_EVENTS = [
    "cycles",
    "L1-dcache-load-misses",
    "branch-misses",
    "context-switches",
    "page-faults",
]

# Alternative set for LLC (run separately to stay within the counter budget)
LLC_EVENTS = [
    "cycles",
    "LLC-load-misses",
    "branch-misses",
    "context-switches",
    "page-faults",
]

# perf event name -> canonical key returned to callers
EVENT_MAP = {
    "cycles":                "cycles",
    "L1-dcache-load-misses": "L1_dcache_misses",
    "LLC-load-misses":       "LLC_misses",
    "branch-misses":         "branch_misses",
    "context-switches":      "context_switches",
    "cs":                    "context_switches",
    "page-faults":           "page_faults",
    "faults":                "page_faults",
    "cache-misses":          "cache_misses",
}


def _parse_perf_stat_output(stderr_text):
    """Parse `perf stat -x,` output.

    Each counter line is:
        <value>,<unit>,<event>,<run-time>,<pct-running>,<metric>,<metric-unit>
    e.g.
        1234567,,cycles,5001234567,100.00,,
        <not counted>,,branch-misses,0,0.00,,

    Returns (counts, multiplexed):
        counts:      dict canonical_name -> int (only events with real values)
        multiplexed: list of event names whose pct-running was < 100
    """
    counts = {}
    multiplexed = []

    for line in stderr_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(",")
        if len(fields) < 3:
            continue

        value, event = fields[0], fields[2]
        # Strip PMU prefixes (cpu/cycles/u) and modifiers (cycles:u)
        if "/" in event:
            event = event.split("/")[1]
        event = event.split(":")[0]
        canonical = EVENT_MAP.get(event)
        if canonical is None:
            continue

        try:
            count = int(float(value))
        except ValueError:
            continue  # <not counted> / <not supported>

        if len(fields) >= 5 and fields[4]:
            try:
                if float(fields[4]) < 100.0:
                    multiplexed.append(event)
            except ValueError:
                pass

        counts[canonical] = count

    return counts, multiplexed


def _run_perf_stat(target_args, command, ruler_cpu, events, timeout,
                   verbose, allow_multiplexed, require_success=False):
    """Run `taskset -c RULER perf stat -x, TARGET -e EVENTS -- COMMAND`.

    Returns dict of counts, or None on failure / multiplexing.
    """
    if events is None:
        events = DEFAULT_EVENTS

    cmd = (
        f"taskset -c {ruler_cpu} "
        f"perf stat -x, {target_args} "
        f"-e {','.join(events)} "
        f"-- {command}"
    )
    if verbose:
        print(f"  [perf_stat_ruler] CMD: {cmd}")

    try:
        result = subprocess.run(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        print(f"  [perf_stat_ruler] TIMEOUT after {timeout}s")
        return None

    if verbose:
        print(f"  [perf_stat_ruler] stderr: {result.stderr[:500]}")

    # perf stat exits with the command's status. For constant-work runs a
    # failed load generator means the window did not contain the work.
    if require_success and result.returncode != 0:
        print(f"  [perf_stat_ruler] command failed (rc={result.returncode}): "
              f"{result.stderr[:200]}")
        return None

    counts, multiplexed = _parse_perf_stat_output(result.stderr)

    if not counts:
        # Common causes: PID gone, perf_event_paranoid too high, event
        # unsupported on this CPU.
        print(f"  [perf_stat_ruler] WARNING: no data parsed. "
              f"returncode={result.returncode} stderr: {result.stderr[:300]}")
        return None

    if multiplexed and not allow_multiplexed:
        print(f"  [perf_stat_ruler] REJECTED: counters multiplexed "
              f"({', '.join(multiplexed)}) — counts would be estimates. "
              f"Reduce events or disable the NMI watchdog "
              f"(sysctl kernel.nmi_watchdog=0).")
        return None

    return counts


def measure_pid_perf_stat(perf_pid, duration, ruler_cpu="14",
                          events=None, verbose=False, allow_multiplexed=False):
    """Count hardware events attributed to perf_pid (all its threads, any
    CPU) for `duration` seconds.

    Returns dict {metric_name: count} or None on failure.
    """
    return _run_perf_stat(
        f"-p {perf_pid}", f"sleep {duration}", ruler_cpu, events,
        timeout=duration + 15, verbose=verbose,
        allow_multiplexed=allow_multiplexed,
    )


def measure_cpu_perf_stat(target_cpu, duration, ruler_cpu="14",
                          events=None, verbose=False, allow_multiplexed=False):
    """Count all hardware events on target_cpu for `duration` seconds.

    Used for the workload baseline (CPU 6). perf stat -C programs the
    target CPU's PMU directly — immune to PID churn of lat_mem_rd.

    Returns dict {metric_name: count} or None on failure.
    """
    return _run_perf_stat(
        f"-C {target_cpu}", f"sleep {duration}", ruler_cpu, events,
        timeout=duration + 15, verbose=verbose,
        allow_multiplexed=allow_multiplexed,
    )


def measure_pid_during_command(target_pid, command, ruler_cpu="14",
                               events=None, timeout=300, verbose=False,
                               allow_multiplexed=False):
    """Count events for target_pid for exactly the lifetime of `command`.

    Constant-work methodology: with command = "hey -n N ...", perf stat
    stops counting the moment the N-th request completes — no idle tail,
    no attach race.

    Returns dict {metric_name: count} or None on failure.
    """
    return _run_perf_stat(
        f"-p {target_pid}", command, ruler_cpu, events,
        timeout=timeout, verbose=verbose,
        allow_multiplexed=allow_multiplexed, require_success=True,
    )


def check_perf_stat_available(ruler_cpu="14", verbose=True):
    """Sanity check: can we run perf stat? Returns True if OK."""
    if verbose:
        print("  Checking perf stat availability...")

    counts = _run_perf_stat(
        "", "sleep 1", ruler_cpu, ["cycles"], timeout=10,
        verbose=False, allow_multiplexed=True,
    )
    if counts and counts.get("cycles", 0) > 0:
        if verbose:
            print(f"  ✅ perf stat OK — measured {counts['cycles']:,} cycles in 1s")
        return True

    print("  ❌ perf stat failed or returned no data.")
    print("  Possible fixes:")
    print("    sudo sysctl -w kernel.perf_event_paranoid=-1")
    print("    OR run with sudo")
    return False
