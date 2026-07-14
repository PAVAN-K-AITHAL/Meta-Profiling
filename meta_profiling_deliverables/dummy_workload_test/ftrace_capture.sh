#!/bin/bash
# ftrace_capture.sh — Capture kernel function graph of perf record
#
# Usage: sudo ./ftrace_capture.sh <WORKLOAD_PID> [DURATION_SECONDS]
#
# System: Intel Xeon E5-2620 v4 (isolated CPUs: 6,7,14,15)
# Pinning:
#   dummy_workload: CPU 6  (Core 6, Socket 0)
#   perf record:    CPU 7  (Core 7, Socket 0)
#   trace-cmd:      CPU 14 (Core 6, Socket 1)
#
# Each layer gets its own isolated physical core.
#
# Prerequisite: sudo apt-get install -y trace-cmd

set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <WORKLOAD_PID> [DURATION_SECONDS]"
    echo "  WORKLOAD_PID: PID of the target application (e.g., dummy_workload)"
    echo "  DURATION_SECONDS: How long to trace (default: 5, keep short!)"
    exit 1
fi

APP_PID="$1"
DURATION="${2:-5}"
SAMPLING_PERIOD="${3:-100000}"
TRACE_FILE="/tmp/perf_ftrace.dat"
REPORT_FILE="/tmp/perf_ftrace_report.txt"

echo "================================================================"
echo "  Ftrace Capture: Kernel Function Graph of perf record"
echo "================================================================"
echo "  Target workload PID:  $APP_PID"
echo "  Trace duration:       ${DURATION}s"
echo "  Sampling period:      -c $SAMPLING_PERIOD"
echo "  Output trace:         $TRACE_FILE"
echo "  Output report:        $REPORT_FILE"
echo ""
echo "  ⚠️  WARNING: ftrace adds ~10-100x overhead to perf record."
echo "  This is for diagnostic purposes only, NOT for measurement."
echo "================================================================"
echo ""

# Verify target process exists
if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "ERROR: Process $APP_PID does not exist."
    exit 1
fi

# Verify trace-cmd is installed
if ! command -v trace-cmd &>/dev/null; then
    echo "ERROR: trace-cmd not found. Install with:"
    echo "  sudo apt-get install -y trace-cmd"
    exit 1
fi

# Clean up any previous traces
sudo trace-cmd reset 2>/dev/null || true
rm -f "$TRACE_FILE" "$REPORT_FILE"

# ----------------------------------------------------------------
# Method 1: Trace perf record with function_graph
# ----------------------------------------------------------------
# -p function_graph: Use the function graph tracer (shows entry/exit/duration)
# -F: Follow the specified command (only trace perf record's kernel calls)
# -o: Output file
# --max-graph-depth 10: Limit call depth to avoid overwhelming output
#
# We wrap perf record inside taskset to pin it to CPU 7,
# and trace-cmd itself runs on CPU 14.
echo ">>> Starting trace-cmd with function_graph tracer..."
echo "    This will run perf record for ${DURATION}s while tracing its kernel calls."
echo ""

sudo taskset -c 14 trace-cmd record \
    -p function_graph \
    --max-graph-depth 10 \
    -F \
    -o "$TRACE_FILE" \
    -- taskset -c 7 perf record -g -e cycles:k -c "$SAMPLING_PERIOD" \
       -p "$APP_PID" -o /tmp/perf-ftrace-data.data \
       -- sleep "$DURATION"

echo ""
echo ">>> Trace capture complete."
echo ""

# Generate the human-readable report
echo ">>> Generating report..."
trace-cmd report -i "$TRACE_FILE" > "$REPORT_FILE" 2>/dev/null

LINE_COUNT=$(wc -l < "$REPORT_FILE")
echo "    Report: $REPORT_FILE ($LINE_COUNT lines)"
echo ""

# Quick preview: show the first 50 lines
echo ">>> Preview (first 50 lines):"
echo "------------------------------"
head -50 "$REPORT_FILE"
echo "------------------------------"
echo ""

# Clean up the perf data file (we don't need the profile data itself)
rm -f /tmp/perf-ftrace-data.data

echo "================================================================"
echo "  ✅ Capture complete!"
echo ""
echo "  Full report:  $REPORT_FILE"
echo "  Raw trace:    $TRACE_FILE"
echo ""
echo "  Next steps:"
echo "    1. Run:  sudo ./ftrace_analyze.sh"
echo "    2. Or:   trace-cmd report -i $TRACE_FILE | less"
echo "    3. Or:   trace-cmd report -i $TRACE_FILE | grep intel_pmu"
echo "================================================================"
