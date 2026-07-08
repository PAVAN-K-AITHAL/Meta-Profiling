#!/bin/bash
# =============================================================================
# measure_overhead_goapi.sh — Single-Shot Meta-Profiling for Go API
# =============================================================================
# Usage: sudo ./measure_overhead_goapi.sh <GO_API_PID> [DURATION_SECONDS]
#
# Same 3-layer architecture as measure_overhead.sh (dummy_workload version):
#   Layer 1 (CPUs 4-5): Go API (target application)        — provided by Docker
#   Layer 2 (CPUs 6-7): perf record (inner instrumentation) — THE THING BEING MEASURED
#   Layer 3 (CPUs 8-9): perf stat (outer instrumentation)   — THE RULER
#
# Differences from dummy_workload version:
#   - Accepts PID from Docker container (docker inspect --format '{{.State.Pid}}')
#   - Default measurement window is 60 seconds (vs 15s for dummy_workload)
#   - Requires k6 load generator to be running separately
# =============================================================================

set -e

# --- Parse arguments ---
if [ -z "$1" ]; then
    echo "Error: No PID provided."
    echo ""
    echo "Usage: sudo $0 <GO_API_PID> [DURATION_SECONDS]"
    echo ""
    echo "  GO_API_PID:        PID of the Go API process (from: docker inspect --format '{{.State.Pid}}' perf-go-api)"
    echo "  DURATION_SECONDS:  Measurement window in seconds (default: 60)"
    echo ""
    echo "Prerequisites:"
    echo "  1. Docker stack running:  docker compose up -d --build"
    echo "  2. k6 load running:      k6 run --duration 30m loadtest/baseline.js"
    exit 1
fi

API_PID=$1
DURATION=${2:-60}  # Default: 60 seconds

# --- Validate PID ---
if [ ! -d "/proc/$API_PID" ]; then
    echo "Error: PID $API_PID does not exist in /proc. Is the Go API container running?"
    exit 1
fi

EVENTS="cpu_core/cycles/,page-faults,cpu_core/branch-misses/,context-switches,cpu_core/cache-misses/"

echo ""
echo "=================================================================="
echo "  Go API Meta-Profiling — Single-Shot Measurement"
echo "=================================================================="
echo "  Target PID:         $API_PID"
echo "  Measurement Window: ${DURATION}s"
echo "  Events:             $EVENTS"
echo "=================================================================="
echo ""

# ============================================================
# Phase 1: BASELINE — Measure the Go API WITHOUT perf record
# ============================================================
echo "Phase 1: Measuring Baseline Go API Metrics (${DURATION}s)..."
echo "  → perf stat on Go API (PID $API_PID), pinned to CPUs 8-9"
sudo taskset -c 8-9 perf stat -x ',' -e $EVENTS -p $API_PID -o /tmp/base_metrics.csv -- sleep $DURATION

echo "  ✅ Baseline complete."
echo ""

# ============================================================
# Phase 2: TOOL OVERHEAD — Start perf record, then measure IT
# ============================================================
echo "Phase 2: Measuring Tool Overhead (${DURATION}s)..."

# Clean up any leftover zombie profilers
sudo pkill -9 -x perf 2>/dev/null || true
sleep 1

# Start perf record on Go API, pinned to CPUs 6-7
echo "  → Starting perf record (inner tool) on CPUs 6-7..."
sudo taskset -c 6-7 perf record -g -e cycles:k -c 100000 -p $API_PID -o /tmp/perf-experiment.data 2>/dev/null &
sleep 2  # Give perf record time to attach and stabilize

# Get the PID of the perf record process
PERF_RECORD_PID=$(pgrep -x perf | head -n 1)

if [ -z "$PERF_RECORD_PID" ]; then
    echo "  ❌ Error: Could not find perf record PID. Did it fail to start?"
    echo "  Try running manually: sudo perf record -g -e cycles:k -c 100000 -p $API_PID -o /tmp/perf.data"
    exit 1
fi

echo "  → perf record started with PID: $PERF_RECORD_PID"
echo "  → Measuring perf record (PID $PERF_RECORD_PID) with perf stat, pinned to CPUs 8-9..."

# Measure perf record's overhead with perf stat
sudo taskset -c 8-9 perf stat -x ',' -e $EVENTS -p $PERF_RECORD_PID -o /tmp/tool_metrics.csv -- sleep $DURATION

echo "  ✅ Tool measurement complete."

# Clean up the background perf record
sudo pkill -9 -x perf 2>/dev/null || true

# ============================================================
# Phase 3: CALCULATE AND DISPLAY RESULTS
# ============================================================
echo ""
echo "==============================================================================================="
echo "  RESULTS: Hardware Overhead Percentages (${DURATION}s window, Go API target)"
echo "==============================================================================================="
printf "%-25s | %-20s | %-20s | %-15s\n" "Metric" "App Baseline" "Tool Overhead" "Overhead %"
echo "--------------------------|----------------------|----------------------|----------------"

for metric in "cpu_core/cycles/" "page-faults" "cpu_core/branch-misses/" "context-switches" "cpu_core/cache-misses/"; do
    # Extract raw numbers from CSV
    app_val=$(grep "$metric" /tmp/base_metrics.csv | awk -F',' '{print $1}')
    tool_val=$(grep "$metric" /tmp/tool_metrics.csv | awk -F',' '{print $1}')

    # Handle empty or <not counted> values
    if [ -z "$app_val" ] || [ "$app_val" == "<not counted>" ]; then app_val=0; fi
    if [ -z "$tool_val" ] || [ "$tool_val" == "<not counted>" ]; then tool_val=0; fi

    # Compute overhead percentage
    if [ "$app_val" -eq 0 ] 2>/dev/null; then
        pct="N/A (App=0)"
    else
        pct=$(awk -v app="$app_val" -v tool="$tool_val" 'BEGIN { printf "%.5f%%", (tool/app)*100 }')
    fi

    # Format numbers with commas for readability
    app_fmt=$(printf "%'d" "$app_val" 2>/dev/null || echo "$app_val")
    tool_fmt=$(printf "%'d" "$tool_val" 2>/dev/null || echo "$tool_val")

    printf "%-25s | %-20s | %-20s | %-15s\n" "$metric" "$app_fmt" "$tool_fmt" "$pct"
done

echo "==============================================================================================="
echo ""
echo "  Temp files:"
echo "    Baseline CSV:  /tmp/base_metrics.csv"
echo "    Tool CSV:      /tmp/tool_metrics.csv"
echo "    perf.data:     /tmp/perf-experiment.data"
echo ""
