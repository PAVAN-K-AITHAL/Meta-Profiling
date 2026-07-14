#!/bin/bash

# trace_perf.sh - Trace the kernel function graph of 'perf record'
#
# This script uses ftrace (via trace-cmd) to trace the kernel functions called by
# perf record to understand *why* the overhead occurs.
#
# Prerequisite: sudo apt-get install trace-cmd

if [ -z "$1" ]; then
    echo "Usage: $0 <PID of the application to profile>"
    exit 1
fi

APP_PID=$1
TRACE_FILE="/tmp/perf_trace.dat"

echo "=================================================="
echo "Tracing 'perf record' kernel functions via ftrace"
echo "=================================================="
echo "This will introduce massive overhead by itself, but provides"
echo "insight into the kernel execution path of perf."
echo ""

# We use trace-cmd to trace the function graph of 'perf record' while it profiles the application.
# -p function_graph: Use the function graph tracer
# -F: Follow only the specified command
# -o: Output file
sudo trace-cmd record -p function_graph -F -o $TRACE_FILE perf record -g -e cycles:k -c 100000 -p $APP_PID -- sleep 2

echo "Tracing complete."
echo "You can read the report by running: trace-cmd report -i $TRACE_FILE | less"
