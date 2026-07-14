# =============================================================================
# Root Makefile — Orchestrator for Meta-Profiling
# =============================================================================
# Runs both experiments sequentially by delegating to the sub-Makefiles.
#
# Usage:   make              (runs both tests)
#          make run-dummy     (dummy workload only)
#          make run-goapi     (Go API only)
# =============================================================================

.PHONY: all run-dummy run-goapi clean

all:
	@echo "╔══════════════════════════════════════════════════════════════════╗"
	@echo "║     Sequential Meta-Profiling — eBPF + Go API                  ║"
	@echo "╚══════════════════════════════════════════════════════════════════╝"
	$(MAKE) -C dummy_workload_test
	@echo "  ⏳ Dummy workload complete. Starting Go API test..."
	@sleep 5
	$(MAKE) -C go_api_test
	@echo "  ✅ ALL EXPERIMENTS COMPLETE"

run-dummy:
	$(MAKE) -C dummy_workload_test

run-goapi:
	$(MAKE) -C go_api_test

clean:
	$(MAKE) -C dummy_workload_test clean
	$(MAKE) -C go_api_test clean
