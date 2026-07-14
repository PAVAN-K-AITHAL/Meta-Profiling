# =============================================================================
# Makefile.goapi — Fully Automated Meta-Profiling for Go API
# =============================================================================
# Just run:   make -f Makefile.goapi
#
# It will automatically:
#   1. Start the Docker microservice stack
#   2. Start k6 load generator in the background
#   3. Run 1000-iteration statistical overhead test (15s window, eBPF outer measurement)
#   4. Stop k6
#   5. Stop the Docker stack
#   6. Save results & auto-push to GitHub
# =============================================================================

.PHONY: all clean

DURATION      = 15
ITERATIONS    = 1000
CONTAINER     = perf-go-api
STACK_DIR     = ../..
EVENTS        = cycles,page-faults,branch-misses,context-switches,cache-misses
RESULTS_DIR   = results
TIMESTAMP     = $(shell date +%Y%m%d_%H%M%S)
RESULTS_FILE  = ../results/goapi_ebpf_overhead_$(TIMESTAMP).csv

all:
	@echo "=================================================================="
	@echo "  Go API Meta-Profiling — Fully Automated"
	@echo "  Iterations: $(ITERATIONS) | Window: $(DURATION)s"
	@echo "  Results will be saved to: $(RESULTS_FILE)"
	@echo "=================================================================="
	@echo ""
	@mkdir -p ../results
	@# ---- Step 1: Start Docker stack ----
	@echo ">>> Step 1/6: Starting Docker microservice stack..."
	cd $(STACK_DIR) && docker compose up -d --build
	@echo "    Waiting 60s for services to become healthy..."
	@sleep 60
	@cd $(STACK_DIR) && docker compose ps
	@echo ""
	@# ---- Step 2: Start k6 in background ----
	@echo ">>> Step 2/6: Starting k6 load generator in background..."
	k6 run --quiet --duration 600m $(STACK_DIR)/loadtest/highload.js > /tmp/k6_output.log 2>&1 & \
	echo $$! > /tmp/k6_pid.txt
	@echo "    k6 PID: $$(cat /tmp/k6_pid.txt)"
	@echo "    Warming up for 30s..."
	@sleep 30
	@# ---- Step 3: Get Go API PID + Step 4: Run test ----
	@echo ">>> Step 3/6: Discovering Go API PID..."
	@API_PID=$$(pgrep -f '^/api$$' | head -n1); \
	if [ -z "$$API_PID" ]; then \
		echo "ERROR: Could not find Go API PID. Is perf-go-api running?"; \
		exit 1; \
	fi; \
	echo "    Go API PID: $$API_PID"; \
	echo ""; \
	echo ">>> Step 4/6: Running eBPF measurement ($(ITERATIONS) iterations × $(DURATION)s)..."; \
	echo "    Estimated time: ~$$(( $(ITERATIONS) * $(DURATION) * 2 / 60 + 5 )) minutes"; \
	echo ""; \
	sudo python3 ../common/measure_statistically_ebpf.py \
		--iterations $(ITERATIONS) \
		--duration $(DURATION) \
		--pid $$API_PID \
		--output $(RESULTS_FILE)
	@echo ""
	@echo ">>> Step 5/6: Stopping k6..."
	@kill $$(cat /tmp/k6_pid.txt) 2>/dev/null || true
	@rm -f /tmp/k6_pid.txt
	@echo "    ✅ k6 stopped."
	@echo ""
	@echo ">>> Step 6/6: Stopping Docker stack & pushing results to GitHub..."
	cd $(STACK_DIR) && docker compose down
	@echo ""
	@# ---- Auto-push results to GitHub ----
	@echo ">>> Committing and pushing results to GitHub..."
	@cd $(STACK_DIR) && \
	sudo -u $${SUDO_USER:-$$USER} -H git config user.name "Perf Auto-Runner" && \
	sudo -u $${SUDO_USER:-$$USER} -H git config user.email "runner@performance-analysis.local" && \
	sudo -u $${SUDO_USER:-$$USER} -H git add -A && \
	sudo -u $${SUDO_USER:-$$USER} -H git commit -m "results: Go API overhead ($(ITERATIONS)×$(DURATION)s) — $$(date '+%Y-%m-%d %H:%M')" && \
	sudo -u $${SUDO_USER:-$$USER} -H git push origin $$(git branch --show-current) && \
	echo "    ✅ Results pushed to GitHub!" || \
	echo "    ⚠️  Git push failed (check auth/network). Results saved locally: $(RESULTS_FILE)"
	@echo ""
	@echo "=================================================================="
	@echo "  ✅ COMPLETE"
	@echo "  Results: $(RESULTS_FILE)"
	@echo "=================================================================="

# ---------------------------------------------------------------------------
# Hypothesis Testing: H1 + H4 (200 iterations, constant work)
# ---------------------------------------------------------------------------
test-hypotheses:
	@echo "=================================================================="
	@echo "  Hypothesis Testing — H1 + H4 ($(ITERATIONS) iterations)"
	@echo "  Constant work: 1000 requests per iteration"
	@echo "=================================================================="
	sudo python3 run_hypotheses_goapi.py --iterations $(ITERATIONS)

test-pilot:
	@echo "=================================================================="
	@echo "  Pilot Run — 50 iterations (Go API)"
	@echo "=================================================================="
	sudo python3 run_hypotheses_goapi.py --pilot

clean:
	@echo "Cleaning up..."
	@kill $$(cat /tmp/k6_pid.txt) 2>/dev/null || true
	@rm -f /tmp/k6_pid.txt /tmp/k6_output.log
	sudo rm -f /tmp/base_metrics.csv /tmp/tool_metrics.csv
	sudo rm -f /tmp/perf-experiment.data
	cd $(STACK_DIR) && docker compose down 2>/dev/null || true
	@echo "✅ Cleaned."
