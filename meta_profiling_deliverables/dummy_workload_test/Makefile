# =============================================================================
# Makefile.ebpf — eBPF-Based Meta-Profiling for Dummy Workload
# =============================================================================
# Usage:
#   make -f Makefile.ebpf              # compile + full 1000-iteration test + push
#   make -f Makefile.ebpf test-single  # quick single-shot sanity check
#   make -f Makefile.ebpf test-stat    # full statistical test only
#   make -f Makefile.ebpf clean        # cleanup
#
# System:  Intel Xeon E5-2620 v4 (32 CPUs, isolated: 6,7,14,15)
# Pinning: workload=CPU6 | perf_record=CPU7 | bpftrace=CPU14
#
# Prerequisites:
#   sudo apt install -y bpftrace
# =============================================================================

.PHONY: all test-single test-stat compile clean

DURATION      = 2
ITERATIONS    = 1000
RESULTS_DIR   = results
TIMESTAMP     = $(shell date +%Y%m%d_%H%M%S)
RESULTS_FILE  = ../results/ebpf_overhead_$(TIMESTAMP).csv

# CPU pinning — isolated CPUs on this system (Xeon E5-2620 v4)
# CPU 6:  Core 6, Socket 0 (HT sibling: CPU 22, not isolated)
# CPU 7:  Core 7, Socket 0 (HT sibling: CPU 23, not isolated)
# CPU 14: Core 6, Socket 1 (HT sibling: CPU 30, not isolated)
# Each layer gets its own isolated physical core.
WORKLOAD_CPU  = 6
TOOL_CPU      = 7
MEASURE_CPU   = 14

all: compile test-stat

# ---------------------------------------------------------------------------
# Compile the dummy workload
# ---------------------------------------------------------------------------
compile:
	@echo "Compiling dummy workload..."
	gcc dummy_workload.c -o dummy_workload

# ---------------------------------------------------------------------------
# Quick single-shot test (< 1 minute) — sanity check
# ---------------------------------------------------------------------------
test-single: compile
	@echo "========================================="
	@echo "  eBPF Single-Shot Overhead Measurement"
	@echo "  workload=CPU$(WORKLOAD_CPU) | perf=CPU$(TOOL_CPU) | bpftrace=CPU$(MEASURE_CPU)"
	@echo "========================================="
	@taskset -c $(WORKLOAD_CPU) ./dummy_workload > /dev/null & \
	PID=$$!; \
	echo "  Workload PID: $$PID (CPU $(WORKLOAD_CPU))"; \
	sleep 1; \
	sudo ./measure_overhead_ebpf.sh $$PID; \
	kill -9 $$PID 2>/dev/null; \
	echo "  Workload killed."

# ---------------------------------------------------------------------------
# Full statistical test (1000 iterations × 2s ≈ 67 minutes)
# Results auto-pushed to GitHub on completion.
# ---------------------------------------------------------------------------
test-stat: compile
	@echo "=================================================================="
	@echo "  eBPF Statistical Overhead Measurement"
	@echo "  Iterations: $(ITERATIONS) | Window: $(DURATION)s"
	@echo "  workload=CPU$(WORKLOAD_CPU) | perf=CPU$(TOOL_CPU) | bpftrace=CPU$(MEASURE_CPU)"
	@echo "  Results: $(RESULTS_FILE)"
	@echo "=================================================================="
	@mkdir -p ../results
	@taskset -c $(WORKLOAD_CPU) ./dummy_workload > /dev/null & \
	PID=$$!; \
	echo "  Workload PID: $$PID (CPU $(WORKLOAD_CPU))"; \
	sleep 1; \
	sudo python3 ../common/measure_statistically_ebpf.py \
		--iterations $(ITERATIONS) \
		--duration $(DURATION) \
		--pid $$PID \
		--output $(RESULTS_FILE); \
	kill -9 $$PID 2>/dev/null; \
	echo "  Workload killed."
	@echo ""
	@# ---- Auto-push results to GitHub ----
	@echo ">>> Committing and pushing results to GitHub..."
	@cd .. && \
	git config user.name "Perf Auto-Runner" && \
	git config user.email "runner@performance-analysis.local" && \
	git add -A && \
	git commit -m "results: eBPF dummy workload overhead ($(ITERATIONS)×$(DURATION)s) — $$(date '+%Y-%m-%d %H:%M')" && \
	git push origin $$(git branch --show-current) && \
	echo "    ✅ Results pushed to GitHub!" || \
	echo "    ⚠️  Git push failed (check auth/network). Results saved locally: $(RESULTS_FILE)"
	@echo ""
	@echo "=================================================================="
	@echo "  ✅ COMPLETE — Results: $(RESULTS_FILE)"
	@echo "=================================================================="

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
clean:
	@echo "Cleaning up..."
	rm -f dummy_workload
	sudo rm -f /tmp/perf-experiment.data
	sudo pkill -9 -x perf 2>/dev/null || true
	sudo pkill -9 -x bpftrace 2>/dev/null || true
	@echo "✅ Cleaned."
