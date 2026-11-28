#!/usr/bin/env python3
"""
perf_record_stats.py — stop perf record cleanly and read its data-quality
counters (lost samples, throttling) from the perf.data it wrote.

Why this matters for the measurement:
  - THROTTLE: the kernel stops delivering samples when the sample rate
    exceeds kernel.perf_event_max_sample_rate (or the CPU-time budget
    kernel.perf_cpu_time_max_percent). A throttled perf record does less
    work than requested, so its measured cost is capped, not real.
  - LOST / LOST_SAMPLES: perf record could not drain the ring buffer fast
    enough and samples were dropped — again, less work than requested.
A run with either is not a valid measurement of the configured -c and is
flagged so the analysis excludes it.

perf record must be stopped with SIGINT and allowed to finish writing
(header + build-id pass); a SIGKILL leaves a truncated perf.data whose
stats cannot be read.
"""

import re
import subprocess
import time

_EVENTS_RE = re.compile(r"^\s*([A-Z_]+) events:\s*(\d+)", re.MULTILINE)


def _perf_running():
    return subprocess.run(["pgrep", "-x", "perf"],
                          stdout=subprocess.DEVNULL).returncode == 0


def stop_perf_record_graceful(timeout=180):
    """SIGINT perf record and wait until it has written its data file.
    Falls back to SIGKILL after `timeout` seconds. Returns True if perf
    exited on its own (data file usable)."""
    subprocess.run("pkill -INT -x perf", shell=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _perf_running():
            return True
        time.sleep(0.2)
    subprocess.run("pkill -9 -x perf", shell=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)
    print(f"  [perf_record_stats] perf record did not exit within {timeout}s; killed")
    return False


def read_record_stats(data_file, timeout=300):
    """Return {'lost_events', 'throttle_events', 'samples'} from a perf.data
    file, or empty strings if it can't be read (empty = unknown, kept
    distinct from a verified 0). 'samples' is the number of SAMPLE records
    perf record actually wrote, used to compute cost per sample."""
    unknown = {"lost_events": "", "throttle_events": "", "samples": ""}
    try:
        res = subprocess.run(
            ["perf", "report", "-i", data_file, "--stats"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  [perf_record_stats] perf report --stats timed out on {data_file}")
        return unknown

    counts = {name: int(n) for name, n in _EVENTS_RE.findall(res.stdout)}
    if "TOTAL" not in counts:
        print(f"  [perf_record_stats] could not parse stats for {data_file}: "
              f"{res.stdout[:200]}")
        return unknown

    # perf only prints non-zero event types, so a missing type means 0.
    return {
        "lost_events": counts.get("LOST", 0) + counts.get("LOST_SAMPLES", 0),
        "throttle_events": counts.get("THROTTLE", 0),
        "samples": counts.get("SAMPLE", 0),
    }


def stop_and_read(data_file):
    """Stop perf record gracefully and return its data-quality counters."""
    if not stop_perf_record_graceful():
        return {"lost_events": "", "throttle_events": "", "samples": ""}
    return read_record_stats(data_file)


# Sampling-rate ceiling. Smallest H2 period is -c 5000; at 2.1 GHz (Turbo
# off) that is ~420K samples/s, so the cap must sit well above it.
MAX_SAMPLE_RATE = 10_000_000


def configure_sample_rate_limits():
    """Lift the kernel's sample-rate cap so perf record runs at the rate -c
    asks for instead of being throttled.

    Order matters: perf_event_max_sample_rate rejects writes once
    perf_cpu_time_max_percent is 0, so set the rate first, then disable the
    dynamic CPU-time throttle (which would otherwise lower the rate again
    and print "perf: interrupt took too long ..."). Not persistent across
    reboots. Returns the values actually in effect.
    """
    # If a previous run already set the percent to 0, the rate write fails
    # with EINVAL; re-enable the percent first so the order always works.
    subprocess.run(["sysctl", "-q", "-w", "kernel.perf_cpu_time_max_percent=25"])
    subprocess.run(["sysctl", "-q", "-w",
                    f"kernel.perf_event_max_sample_rate={MAX_SAMPLE_RATE}"])
    subprocess.run(["sysctl", "-q", "-w", "kernel.perf_cpu_time_max_percent=0"])
    vals = {}
    for k in ("perf_event_max_sample_rate", "perf_cpu_time_max_percent"):
        with open(f"/proc/sys/kernel/{k}") as f:
            vals[k] = int(f.read().strip())
    print(f"  Sample-rate limits: max_sample_rate={vals['perf_event_max_sample_rate']:,} "
          f"cpu_time_max_percent={vals['perf_cpu_time_max_percent']}")
    if vals["perf_event_max_sample_rate"] < MAX_SAMPLE_RATE or vals["perf_cpu_time_max_percent"] != 0:
        print("  ⚠️ Could not lift sample-rate limits — small -c runs will be "
              "throttled and excluded by the analysis.")
    return vals
