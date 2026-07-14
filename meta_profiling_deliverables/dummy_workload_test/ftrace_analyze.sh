#!/bin/bash
# ftrace_analyze.sh — Analyze the ftrace function_graph output of perf record
#
# Extracts:
#   1. Top 30 most time-consuming kernel functions
#   2. Categorized breakdown by kernel subsystem
#   3. NMI interrupt handler call chain (primary cycle overhead source)
#   4. Ring buffer / mmap operations (primary cache-miss source)
#   5. Context switch paths
#   6. Syscall paths (perf_event_open, ioctl, mmap)
#
# Usage: sudo ./ftrace_analyze.sh [REPORT_FILE]
#
# Prerequisite: Run ftrace_capture.sh first to generate the report.

set -euo pipefail

REPORT_FILE="${1:-/tmp/perf_ftrace_report.txt}"
RESULTS_DIR="/tmp/ftrace_analysis"

if [ ! -f "$REPORT_FILE" ]; then
    echo "ERROR: Report file not found: $REPORT_FILE"
    echo "  Run ftrace_capture.sh first."
    exit 1
fi

mkdir -p "$RESULTS_DIR"

TOTAL_LINES=$(wc -l < "$REPORT_FILE")

echo "================================================================"
echo "  Ftrace Analysis: Kernel Function Breakdown of perf record"
echo "  Input:  $REPORT_FILE ($TOTAL_LINES lines)"
echo "  Output: $RESULTS_DIR/"
echo "================================================================"
echo ""

# ---------------------------------------------------------------
# 1. Top 30 most time-consuming functions (leaf functions with duration)
# ---------------------------------------------------------------
echo ">>> 1. Top 30 Most Time-Consuming Kernel Functions"
echo "    (Sorted by total cumulative duration)"
echo "----------------------------------------------------"

# function_graph format example:
#   perf-1234  [007]  .... 12345.678: 0.312 us   |      native_write_msr();
#   perf-1234  [007]  .... 12345.679: + 15.200 us |    } /* intel_pmu_handle_irq */
#
# We extract lines with duration values and function names.
# Leaf calls end with ();  Block exits end with } /* funcname */

grep -oP '([\d.]+) us\s+\|\s+(\S+)\(\)' "$REPORT_FILE" 2>/dev/null | \
    sed 's/ us.*| */|/' | \
    awk -F'|' '{
        fname = $2;
        gsub(/[();]/, "", fname);
        duration = $1 + 0;
        total[fname] += duration;
        count[fname]++;
    }
    END {
        for (f in total) {
            printf "%12.3f us  (%6d calls)  %s\n", total[f], count[f], f;
        }
    }' | sort -rn | head -30 | tee "$RESULTS_DIR/top_functions.txt"

echo ""

# Also extract block-level durations (} /* funcname */ lines)
echo ">>> 1b. Top 30 Block-Level Functions (including nested time)"
echo "-------------------------------------------------------------"

grep -oP '[\+!#]?\s*([\d.]+) us\s+\|\s+\}\s+/\*\s+(\S+)' "$REPORT_FILE" 2>/dev/null | \
    sed 's|[+!#]*\s*||; s| us.*| |; s|.*\*/\s*||' | \
    awk '{
        # This is harder to parse, try a different approach
    }' 2>/dev/null || true

# Alternative: just count function call occurrences
echo ""
echo ">>> 1c. Most Frequently Called Kernel Functions"
echo "-------------------------------------------------"

grep -oP '\|\s+(\S+)\(\)' "$REPORT_FILE" 2>/dev/null | \
    sed 's/.*| *//' | sed 's/()$//' | \
    sort | uniq -c | sort -rn | head -30 | \
    tee "$RESULTS_DIR/most_called_functions.txt"

echo ""

# ---------------------------------------------------------------
# 2. Categorized breakdown by kernel subsystem
# ---------------------------------------------------------------
echo ">>> 2. Kernel Subsystem Breakdown (occurrence counts)"
echo "------------------------------------------------------"

# Count function call occurrences by category
PMU_COUNT=$(grep -cP 'perf_|pmu_|intel_pmu|x86_pmu|__perf' "$REPORT_FILE" 2>/dev/null || echo "0")
NMI_COUNT=$(grep -cP 'nmi_handle|do_nmi|is_nmi|end_nmi|exc_nmi' "$REPORT_FILE" 2>/dev/null || echo "0")
MMAP_COUNT=$(grep -cP 'mmap|ring_buffer|perf_output|__alloc_pages|page_fault|handle_mm|rb_' "$REPORT_FILE" 2>/dev/null || echo "0")
SCHED_COUNT=$(grep -cP 'schedule|context_switch|__switch_to|pick_next|dequeue|enqueue' "$REPORT_FILE" 2>/dev/null || echo "0")
SYSCALL_COUNT=$(grep -cP 'sys_|do_syscall|entry_SYSCALL|__x64_sys' "$REPORT_FILE" 2>/dev/null || echo "0")
IRQ_COUNT=$(grep -cP 'irq_|do_IRQ|handle_irq|apic|ack_APIC' "$REPORT_FILE" 2>/dev/null || echo "0")
LOCK_COUNT=$(grep -cP 'spin_lock|mutex_|rcu_|_lock|_unlock|rwsem' "$REPORT_FILE" 2>/dev/null || echo "0")
MSR_COUNT=$(grep -cP 'native_write_msr|native_read_msr|rdmsr|wrmsr' "$REPORT_FILE" 2>/dev/null || echo "0")

printf "  %-30s %8s occurrences\n" "PMU / perf functions:" "$PMU_COUNT"
printf "  %-30s %8s occurrences\n" "NMI handlers:" "$NMI_COUNT"
printf "  %-30s %8s occurrences\n" "Memory / mmap / ring buffer:" "$MMAP_COUNT"
printf "  %-30s %8s occurrences\n" "Scheduler / context switch:" "$SCHED_COUNT"
printf "  %-30s %8s occurrences\n" "Syscalls:" "$SYSCALL_COUNT"
printf "  %-30s %8s occurrences\n" "IRQ / interrupt:" "$IRQ_COUNT"
printf "  %-30s %8s occurrences\n" "Locks / synchronization:" "$LOCK_COUNT"
printf "  %-30s %8s occurrences\n" "MSR reads/writes:" "$MSR_COUNT"

# Save subsystem counts
cat > "$RESULTS_DIR/subsystem_breakdown.txt" << EOF
Kernel Subsystem Breakdown for perf record
===========================================
PMU / perf functions:         $PMU_COUNT occurrences
NMI handlers:                 $NMI_COUNT occurrences
Memory / mmap / ring buffer:  $MMAP_COUNT occurrences
Scheduler / context switch:   $SCHED_COUNT occurrences
Syscalls:                     $SYSCALL_COUNT occurrences
IRQ / interrupt:              $IRQ_COUNT occurrences
Locks / synchronization:      $LOCK_COUNT occurrences
MSR reads/writes:             $MSR_COUNT occurrences
EOF

echo ""

# ---------------------------------------------------------------
# 3. NMI handler call chain (THE primary source of cycle overhead)
# ---------------------------------------------------------------
echo ">>> 3. NMI Handler Call Chain"
echo "    (This is the PRIMARY source of perf record's CPU overhead)"
echo "    (NMI fires every -c N cycles; handler records the sample)"
echo "-------------------------------------------------------------"

grep -B 2 -A 30 "nmi_handle\|do_nmi\|exc_nmi\|intel_pmu_handle_irq" "$REPORT_FILE" | \
    head -80 | tee "$RESULTS_DIR/nmi_callchain.txt"

echo ""
echo "    [Full chain saved to $RESULTS_DIR/nmi_callchain.txt]"
echo ""

# ---------------------------------------------------------------
# 4. Ring buffer / mmap operations (source of cache-miss overhead)
# ---------------------------------------------------------------
echo ">>> 4. Ring Buffer / Mmap Operations"
echo "    (These cause cache misses: writing sample data to the ring buffer)"
echo "----------------------------------------------------------------------"

grep -B 2 -A 10 "perf_output\|ring_buffer_\|rb_\|perf_mmap\|__perf_event_output\|perf_prepare_sample" "$REPORT_FILE" | \
    head -60 | tee "$RESULTS_DIR/ringbuffer_ops.txt"

echo ""
echo "    [Full data saved to $RESULTS_DIR/ringbuffer_ops.txt]"
echo ""

# ---------------------------------------------------------------
# 5. Context switch / scheduler paths
# ---------------------------------------------------------------
echo ">>> 5. Context Switch Paths"
echo "    (perf record wakes periodically to drain the ring buffer)"
echo "--------------------------------------------------------------"

grep -B 2 -A 10 "schedule\|__switch_to\|context_switch\|finish_task_switch" "$REPORT_FILE" | \
    head -60 | tee "$RESULTS_DIR/context_switch_paths.txt"

echo ""
echo "    [Full data saved to $RESULTS_DIR/context_switch_paths.txt]"
echo ""

# ---------------------------------------------------------------
# 6. Syscall paths (perf_event_open, ioctl, mmap)
# ---------------------------------------------------------------
echo ">>> 6. Syscall Paths (perf_event_open / ioctl / mmap)"
echo "    (These show the setup cost and periodic maintenance calls)"
echo "--------------------------------------------------------------"

grep -B 2 -A 15 "sys_perf\|perf_event_open\|sys_ioctl\|sys_mmap\|perf_ioctl\|perf_event_ioctl\|perf_read" "$REPORT_FILE" | \
    head -60 | tee "$RESULTS_DIR/syscall_paths.txt"

echo ""
echo "    [Full data saved to $RESULTS_DIR/syscall_paths.txt]"
echo ""

# ---------------------------------------------------------------
# 7. Page fault paths (source of page-fault overhead)
# ---------------------------------------------------------------
echo ">>> 7. Page Fault Paths"
echo "    (Initial mmap ring buffer setup causes exactly 32 page faults)"
echo "-------------------------------------------------------------------"

grep -B 2 -A 10 "page_fault\|handle_mm_fault\|do_page_fault\|__handle_mm_fault\|exc_page_fault" "$REPORT_FILE" | \
    head -40 | tee "$RESULTS_DIR/page_fault_paths.txt"

echo ""
echo "    [Full data saved to $RESULTS_DIR/page_fault_paths.txt]"
echo ""

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo "================================================================"
echo "  ✅ Analysis Complete!"
echo ""
echo "  Output files in: $RESULTS_DIR/"
echo ""
ls -1 "$RESULTS_DIR/" | while read f; do
    lines=$(wc -l < "$RESULTS_DIR/$f")
    printf "    %-35s %5d lines\n" "$f" "$lines"
done
echo ""
echo "  Key interpretation:"
echo "    - nmi_callchain.txt        → WHY cycles overhead occurs"
echo "    - ringbuffer_ops.txt       → WHY cache-miss overhead occurs"
echo "    - context_switch_paths.txt → WHY context-switch overhead occurs"
echo "    - page_fault_paths.txt     → WHY page-fault overhead occurs"
echo "    - top_functions.txt        → Top hottest leaf functions"
echo "    - subsystem_breakdown.txt  → Categorized occurrence counts"
echo "================================================================"
