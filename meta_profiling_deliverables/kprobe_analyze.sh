#!/bin/bash
# kprobe_analyze.sh
# 
# Runs the dummy workload, attaches eBPF kprobes, and then starts perf record.

set -euo pipefail

echo "================================================================"
echo "  eBPF Kprobe Diagnostic: Profiler Latency Analysis"
echo "================================================================"

# 1. Start dummy workload
taskset -c 6 ./dummy_workload > /dev/null &
WORKLOAD_PID=$!
echo "[+] Started target dummy workload on CPU 6 (PID: $WORKLOAD_PID)"

# 2. Start bpftrace in the background, filtering on the workload PID for the NMIs
echo "[+] Starting bpftrace and attaching probes..."
bpftrace ./kprobe_diagnostic.bt $WORKLOAD_PID > /tmp/kprobe_output.txt 2>&1 &
BPF_PID=$!

# Wait for bpftrace to attach probes
sleep 3

# 3. Start perf record
echo "[+] Started perf record on CPU 7. Capturing data..."
taskset -c 7 perf record -g -e cycles:k -c 100000 -p $WORKLOAD_PID -o /tmp/kprobe-perf.data 2>/dev/null &
PERF_PID=$!

# Let it profile for 5 seconds
sleep 5

echo "[+] Trace complete. Stopping processes..."
kill -9 $PERF_PID $WORKLOAD_PID 2>/dev/null || true

# 4. Trigger bpftrace to print histograms by sending SIGINT
kill -INT $BPF_PID
sleep 1

cat /tmp/kprobe_output.txt
rm -f /tmp/kprobe-perf.data /tmp/kprobe_output.txt

echo "================================================================"
echo "  ✅ Kprobe Analysis Finished!"
echo "================================================================"


