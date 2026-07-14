#!/bin/bash
# =============================================================================
# measure_overhead_ebpf.sh — Single-Shot eBPF Overhead Measurement
# =============================================================================
# Usage: sudo ./measure_overhead_ebpf.sh <TARGET_PID>
#
# Uses bpftrace (eBPF) instead of perf stat as the outer measurement layer.
# Runs two phases:
#   Phase 1: Baseline — measures the target process events (no profiler)
#   Phase 2: Tool     — starts perf record, then measures perf record's events
#
# CPU pinning (Intel Xeon E5-2620 v4, isolated CPUs: 6,7,14,15):
#   Target workload:  CPU 6  (passed externally via taskset)
#   perf record:      CPU 7  (inner tool being measured)
#   bpftrace:         CPU 14 (outer measurement — the ruler)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EBPF_SCRIPT="$SCRIPT_DIR/ebpf_counter.bt"
DURATION=15
MEASURE_CPU=14
TOOL_CPU=7

# --- Argument check ---
if [ -z "$1" ]; then
    echo "Usage: $0 <TARGET_PID>"
    echo ""
    echo "  TARGET_PID: PID of the process to profile (e.g., dummy_workload)"
    exit 1
fi
TARGET_PID=$1

# --- Pre-flight checks ---
if ! command -v bpftrace &>/dev/null; then
    echo "ERROR: bpftrace is not installed."
    echo "  Install with: sudo apt install -y bpftrace"
    exit 1
fi

if [ ! -f "$EBPF_SCRIPT" ]; then
    echo "ERROR: eBPF counter script not found: $EBPF_SCRIPT"
    exit 1
fi

echo "Target PID:     $TARGET_PID"
echo "eBPF script:    $EBPF_SCRIPT"
echo "Measurement:    CPU $MEASURE_CPU (bpftrace)"
echo "Tool under test: CPU $TOOL_CPU (perf record)"
echo "Duration:       ${DURATION}s per phase"
echo ""

# =========================================================================
# Phase 1: Baseline — measure the target process (no profiler running)
# =========================================================================
echo "=========================================="
echo "Phase 1: eBPF Baseline (no profiler)"
echo "=========================================="
echo "  Measuring workload PID $TARGET_PID for ${DURATION}s..."
BASELINE_OUTPUT=$(sudo bpftrace $EBPF_SCRIPT $TARGET_PID $DURATION 2>/dev/null)
echo "$BASELINE_OUTPUT" | head -2

# =========================================================================
# Phase 2: Tool Overhead — measure perf record's own footprint
# =========================================================================
echo ""
echo "=========================================="
echo "Phase 2: eBPF Tool Overhead"
echo "=========================================="

# Clean up any leftover profilers
sudo pkill -9 -x perf 2>/dev/null

# Start perf record profiling the target workload
echo "  Starting perf record (CPU $TOOL_CPU) profiling PID $TARGET_PID..."
sudo taskset -c $TOOL_CPU perf record -g -e cycles:k -c 100000 \
    -p $TARGET_PID -o /tmp/perf-experiment.data 2>/dev/null &
sleep 1

PERF_PID=$(pgrep -x perf | head -n 1)
if [ -z "$PERF_PID" ]; then
    echo "ERROR: Could not find perf record PID."
    exit 1
fi

echo "  Measuring perf record PID $PERF_PID for ${DURATION}s..."
TOOL_OUTPUT=$(sudo bpftrace $EBPF_SCRIPT $PERF_PID $DURATION 2>/dev/null)
echo "$TOOL_OUTPUT" | head -2

# Kill perf record
sudo pkill -9 -x perf 2>/dev/null

# =========================================================================
# Results — parse CSV and compute overhead percentages
# =========================================================================
echo ""
echo "========================================================================="
echo "RESULTS: eBPF Hardware Overhead (${DURATION}s window)"
echo "  Outer measurement: bpftrace (eBPF) | Tool: perf record -c 100000"
echo "========================================================================="
printf "%-20s | %-18s | %-18s | %-15s\n" "Metric" "App Baseline" "Tool Overhead" "Overhead %"
echo "---------------------|--------------------|--------------------|-----------------"

for metric in cycles cache-misses branch-misses page-faults context-switches; do
    # Extract CSV lines from captured output
    base_line=$(echo "$BASELINE_OUTPUT" | grep "^${metric}," | head -1)
    tool_line=$(echo "$TOOL_OUTPUT" | grep "^${metric}," | head -1)

    if [ -n "$base_line" ] && [ -n "$tool_line" ]; then
        # Parse: metric,raw_count,period
        base_count=$(echo "$base_line" | cut -d',' -f2)
        base_period=$(echo "$base_line" | cut -d',' -f3)
        tool_count=$(echo "$tool_line" | cut -d',' -f2)
        tool_period=$(echo "$tool_line" | cut -d',' -f3)

        # Extrapolate: value = count × period
        base_val=$((base_count * base_period))
        tool_val=$((tool_count * tool_period))

        if [ "$base_val" -eq 0 ]; then
            pct="N/A (base=0)"
        else
            pct=$(awk -v b="$base_val" -v t="$tool_val" 'BEGIN { printf "%.5f%%", (t/b)*100 }')
        fi

        base_fmt=$(printf "%'d" "$base_val")
        tool_fmt=$(printf "%'d" "$tool_val")
        printf "%-20s | %-18s | %-18s | %-15s\n" "$metric" "$base_fmt" "$tool_fmt" "$pct"
    else
        printf "%-20s | %-18s | %-18s | %-15s\n" "$metric" "—" "—" "no data"
    fi
done
echo "========================================================================="
echo ""
echo "Note: Hardware events (cycles, cache-misses, branch-misses) are extrapolated"
echo "      from overflow-based sampling. Software events (page-faults, context-switches)"
echo "      are exact counts."
