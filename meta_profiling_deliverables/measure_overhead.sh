#!/bin/bash

if [ -n "$1" ]; then
    API_PID=$1
    echo "Using provided TARGET_PID: $API_PID"
else
    echo "Make sure your k6 load test is running in another terminal!"
    echo "Waiting 3 seconds..."
    sleep 3

    # Try pgrep first (works when container uses pid:host)
    API_PID=$(pgrep -f '^/api$' | head -n1)
    if [ -z "$API_PID" ]; then
        # Fallback: docker inspect
        API_PID=$(docker inspect --format '{{.State.Pid}}' perf-go-api 2>/dev/null)
    fi
    if [ -z "$API_PID" ] || [ "$API_PID" = "0" ]; then
        echo "Error: Could not find Go API PID. Provide a PID as an argument or ensure the container is running."
        echo "Usage: $0 [TARGET_PID]"
        exit 1
    fi
fi

EVENTS="cpu_core/cycles/,page-faults,cpu_core/branch-misses/,context-switches,cpu_core/cache-misses/"

echo "=========================================="
echo "Phase 1: Measuring Baseline Go API Metrics"
echo "=========================================="
# We use -x ',' to format the output as CSV so the script can parse the numbers
sudo taskset -c 8-9 perf stat -x ',' -e $EVENTS -p $API_PID -o /tmp/base_metrics.csv -- sleep 15

echo "=========================================="
echo "Phase 2: Measuring Tool Overhead"
echo "=========================================="
# Clean up any leftover zombie profilers first
sudo pkill -9 -x perf 2>/dev/null

# Start perf record pinned to the P-cores
sudo taskset -c 6-7 perf record -g -e cycles:k -c 100000 -p $API_PID -o /tmp/perf-experiment.data 2>/dev/null &
sleep 1

# Get the PID of the perf record we just started
PERF_RECORD_PID=$(pgrep -x perf | head -n 1)

# Measure the tool
sudo taskset -c 8-9 perf stat -x ',' -e $EVENTS -p $PERF_RECORD_PID -o /tmp/tool_metrics.csv -- sleep 15

# Clean up the background perf record
sudo pkill -9 -x perf

echo ""
echo "========================================================================="
echo "RESULTS: Hardware Overhead Percentages (15 second window)"
echo "========================================================================="
printf "%-25s | %-15s | %-15s | %-15s\n" "Metric" "App Baseline" "Tool Overhead" "Overhead %"
echo "--------------------------|-----------------|-----------------|-----------------"

# Read metrics one by one and calculate
for metric in "cpu_core/cycles/" "page-faults" "cpu_core/branch-misses/" "context-switches" "cpu_core/cache-misses/"; do
    # Extract the raw numbers from the CSV outputs
    app_val=$(grep "$metric" /tmp/base_metrics.csv | awk -F',' '{print $1}')
    tool_val=$(grep "$metric" /tmp/tool_metrics.csv | awk -F',' '{print $1}')
    
    # Handle empty or <not counted> values
    if [ -z "$app_val" ] || [ "$app_val" == "<not counted>" ]; then app_val=0; fi
    if [ -z "$tool_val" ] || [ "$tool_val" == "<not counted>" ]; then tool_val=0; fi

    # Compute percentage using awk (so we can do floating point math)
    if [ "$app_val" -eq 0 ]; then
        pct="N/A (App used 0)"
    else
        pct=$(awk -v app="$app_val" -v tool="$tool_val" 'BEGIN { printf "%.5f%%", (tool/app)*100 }')
    fi
    
    # Format numbers with commas for readability, then print row
    app_fmt=$(printf "%'d" "$app_val")
    tool_fmt=$(printf "%'d" "$tool_val")
    printf "%-25s | %-15s | %-15s | %-15s\n" "$metric" "$app_fmt" "$tool_fmt" "$pct"
done
echo "========================================================================="
