# =============================================================================
# Root Makefile — Orchestrator for Meta-Profiling
# =============================================================================
# Usage:
#   make                   (legacy: runs both old tests)
#   make harden            System hardening (run first!)
#   make pilot             Pilot run: 50 iterations (quick, ~30 min)
#   make run-hypotheses    Full hypothesis suite: H1+H2+H3 (dummy) + H1+H4 (Go API)
#   make run-dummy-hyp     H1+H2+H3 on dummy workload only
#   make run-goapi-hyp     H1+H4 on Go API only
#   make analyze           Statistical analysis on collected data
#   make run-all           harden → run-hypotheses → analyze (full pipeline)
# =============================================================================

.PHONY: all run-dummy run-goapi clean harden pilot run-hypotheses run-dummy-hyp run-goapi-hyp analyze run-all

ITERATIONS    ?= 200
PILOT_ITERS   = 50
RESULTS_DIR   = results

# --- Legacy targets (unchanged) ---

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

# --- New hypothesis testing targets ---

harden:
	@echo "╔══════════════════════════════════════════════════════════════════╗"
	@echo "║     System Hardening                                           ║"
	@echo "╚══════════════════════════════════════════════════════════════════╝"
	sudo bash common/system_harden.sh

harden-check:
	sudo bash common/system_harden.sh --check

harden-revert:
	sudo bash common/system_harden.sh --revert

pilot:
	@echo "╔══════════════════════════════════════════════════════════════════╗"
	@echo "║     Pilot Run — 50 Iterations (Quick)                          ║"
	@echo "╚══════════════════════════════════════════════════════════════════╝"
	cd dummy_workload_test && sudo python3 run_hypotheses_dummy.py --pilot
	@echo "  ✅ Pilot complete. Check results/ for CSV output."

run-dummy-hyp:
	@echo "╔══════════════════════════════════════════════════════════════════╗"
	@echo "║     Hypothesis Testing — Dummy Workload (H1+H2+H3)            ║"
	@echo "║     Iterations: $(ITERATIONS)                                        ║"
	@echo "╚══════════════════════════════════════════════════════════════════╝"
	cd dummy_workload_test && sudo python3 run_hypotheses_dummy.py --iterations $(ITERATIONS)

run-goapi-hyp:
	@echo "╔══════════════════════════════════════════════════════════════════╗"
	@echo "║     Hypothesis Testing — Go API (H1+H4)                        ║"
	@echo "║     Iterations: $(ITERATIONS)                                        ║"
	@echo "╚══════════════════════════════════════════════════════════════════╝"
	cd go_api_test && sudo python3 run_hypotheses_goapi.py --iterations $(ITERATIONS)

run-hypotheses: run-dummy-hyp
	@echo "  ⏳ Dummy workload hypotheses complete. Starting Go API..."
	@sleep 5
	$(MAKE) run-goapi-hyp
	@echo "  ✅ ALL HYPOTHESIS EXPERIMENTS COMPLETE"

analyze:
	@echo "╔══════════════════════════════════════════════════════════════════╗"
	@echo "║     Statistical Analysis                                       ║"
	@echo "╚══════════════════════════════════════════════════════════════════╝"
	@DUMMY_CSV=$$(ls -t $(RESULTS_DIR)/hypotheses_dummy_*.csv 2>/dev/null | head -1); \
	GOAPI_CSV=$$(ls -t $(RESULTS_DIR)/hypotheses_goapi_*.csv 2>/dev/null | head -1); \
	if [ -z "$$DUMMY_CSV" ]; then \
		echo "  ERROR: No dummy workload results found. Run 'make run-dummy-hyp' first."; \
		exit 1; \
	fi; \
	echo "  Dummy CSV: $$DUMMY_CSV"; \
	GOAPI_FLAG=""; \
	if [ -n "$$GOAPI_CSV" ]; then \
		echo "  Go API CSV: $$GOAPI_CSV"; \
		GOAPI_FLAG="--goapi $$GOAPI_CSV"; \
	fi; \
	python3 common/analyze_hypotheses.py $$DUMMY_CSV $$GOAPI_FLAG \
		--output $(RESULTS_DIR)/hypothesis_results_report.md

run-all: harden run-hypotheses analyze
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════╗"
	@echo "║     ✅ FULL PIPELINE COMPLETE                                  ║"
	@echo "║     Report: $(RESULTS_DIR)/hypothesis_results_report.md        ║"
	@echo "╚══════════════════════════════════════════════════════════════════╝"

clean:
	$(MAKE) -C dummy_workload_test clean
	$(MAKE) -C go_api_test clean
