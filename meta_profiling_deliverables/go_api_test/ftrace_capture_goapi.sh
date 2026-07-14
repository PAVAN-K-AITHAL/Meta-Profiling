#!/bin/bash
# ftrace_capture_goapi.sh — Capture kernel function graph of perf record on Go API
#
# Usage: sudo ./ftrace_capture_goapi.sh [DURATION_SECONDS]
#
# System: Intel Xeon E5-2620 v4 (isolated CPUs: 6,7,14,15)
# Pinning:
#   Go API:         unpinned (runs on non-isolated CPUs)
#   perf record:    CPU 7  (Core 7, Socket 0)
#   trace-cmd:      CPU 14 (Core 6, Socket 1)
#
# PURPOSE: Ftrace is used ONLY to untangle complex kernel function call chains.
#          It is NEVER used in production because of its MASSIVE overhead (~10-100x).
#          This is a diagnostic tool to explain WHY perf record has the overhead it has,
#          NOT to measure the overhead itself.
#
# Prerequisite: sudo apt-get install -y trace-cmd

set -euo pipefail

DURATION="${1:-5}"
SAMPLING_PERIOD="${2:-100000}"
TRACE_FILE="/tmp/goapi_perf_ftrace.dat"
REPORT_FILE="/tmp/goapi_perf_ftrace_report.txt"

echo "================================================================"
echo "  Ftrace Capture: Kernel Function Graph of perf record on Go API"
echo "================================================================"
echo "  Trace duration:       ${DURATION}s"
echo "  Sampling period:      -c $SAMPLING_PERIOD"
echo "  Output trace:         $TRACE_FILE"
echo "  Output report:        $REPORT_FILE"
echo ""
echo "  ⚠️  WARNING: ftrace adds ~10-100x overhead to perf record."
echo "  This is for diagnostic purposes only, NOT for measurement."
echo "  We use it to understand the kernel call chains that cause overhead."
echo "================================================================"
echo ""

# Find Go API PID
APP_PID=$(pgrep -f '^/api$' | head -n1)
if [ -z "$APP_PID" ]; then
    echo "ERROR: Go API process not found. Is docker compose running?"
    exit 1
fi
echo "  Go API PID: $APP_PID"

# Verify trace-cmd is installed
if ! command -v trace-cmd &>/dev/null; then
    echo "ERROR: trace-cmd not found. Install with:"
    echo "  sudo apt-get install -y trace-cmd"
    exit 1
fi

# Clean up any previous traces
sudo trace-cmd reset 2>/dev/null || true
rm -f "$TRACE_FILE" "$REPORT_FILE"

echo ""
echo ">>> Starting trace-cmd with function_graph tracer..."
echo "    This will run perf record for ${DURATION}s while tracing its kernel calls."
echo ""

sudo taskset -c 14 trace-cmd record \
    -p function_graph \
    --max-graph-depth 10 \
    -F \
    -o "$TRACE_FILE" \
    -- taskset -c 7 perf record -g -e cycles:k -c "$SAMPLING_PERIOD" \
       -p "$APP_PID" -o /tmp/goapi-perf-ftrace-data.data \
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

# Quick preview
echo ">>> Preview (first 50 lines):"
echo "------------------------------"
head -50 "$REPORT_FILE"
echo "------------------------------"
echo ""

# Clean up the perf data file
rm -f /tmp/goapi-perf-ftrace-data.data

echo "================================================================"
echo "  ✅ Capture complete!"
echo ""
echo "  Full report:  $REPORT_FILE"
echo "  Raw trace:    $TRACE_FILE"
echo ""
echo "  Next steps:"
echo "    1. Run:  sudo ./ftrace_analyze_goapi.sh"
echo "    2. Or:   trace-cmd report -i $TRACE_FILE | less"
echo "    3. Or:   trace-cmd report -i $TRACE_FILE | grep __perf_event"
echo "================================================================"
