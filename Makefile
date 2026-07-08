# =============================================================
# Performance Observatory — Makefile
# Worker 4: Orchestration
# =============================================================
# IMPORTANT: Makefile recipes MUST use TAB characters, not spaces.
# Run `cat -A Makefile | grep "^I"` to verify tab indentation.
#
# Usage:
#   make up           — Start the full stack (detached)
#   make down         — Stop and remove containers
#   make build        — Rebuild images only (no start)
#   make logs         — Tail logs from all services
#   make test-health  — Smoke test: /healthz endpoint
#   make load-baseline — Run steady-state load test (requires k6)
# =============================================================

COMPOSE  := docker compose
K6       := docker run --rm -i --network host -v $$(pwd):/src -w /src grafana/k6
BASE_URL := http://localhost

.PHONY: up down build restart logs ps \
        test-health test-users test-compute test-mixed test-metrics \
        load-baseline load-stress load-spike \
        clean nuke help

# -----------------------------------------------------------
# Stack Lifecycle
# -----------------------------------------------------------

## up: Start entire stack in detached mode (builds images if missing)
up:
	$(COMPOSE) up -d --build
	@echo ""
	@echo "✅ Stack is up. Services:"
	@echo "   Nginx:      http://localhost"
	@echo "   Go API:     http://localhost:8080"
	@echo "   Prometheus: http://localhost:9090"
	@echo "   Grafana:    http://localhost:3000  (admin / admin)"
	@echo ""
	@echo "Waiting for services to be healthy..."
	@sleep 5
	$(COMPOSE) ps

## down: Stop and remove all containers (preserves volumes)
down:
	$(COMPOSE) down

## build: Build/rebuild Docker images without starting containers
build:
	$(COMPOSE) build --no-cache

## restart: Restart all services
restart:
	$(COMPOSE) restart

## logs: Follow logs from all services (Ctrl+C to stop)
logs:
	$(COMPOSE) logs -f

## ps: Show status of all services
ps:
	$(COMPOSE) ps

# -----------------------------------------------------------
# Smoke Tests — Quick endpoint validation
# -----------------------------------------------------------

## test-health: Check Go API health endpoint
test-health:
	@echo "🔍 Testing /healthz ..."
	@curl -sf $(BASE_URL)/healthz | python3 -m json.tool || \
		(echo "❌ /healthz failed" && exit 1)
	@echo "✅ /healthz OK"

## test-users: Fetch 3 random users from PostgreSQL
test-users:
	@echo "🔍 Testing /users?n=3 ..."
	@curl -sf "$(BASE_URL)/users?n=3" | python3 -m json.tool || \
		(echo "❌ /users failed" && exit 1)
	@echo "✅ /users OK"

## test-compute: CPU-bound SHA-256 hashing endpoint
test-compute:
	@echo "🔍 Testing POST /compute?n=100 ..."
	@curl -sf -X POST "$(BASE_URL)/compute?n=100" | python3 -m json.tool || \
		(echo "❌ /compute failed" && exit 1)
	@echo "✅ /compute OK"

## test-mixed: Combined DB + CPU endpoint
test-mixed:
	@echo "🔍 Testing /mixed ..."
	@curl -sf "$(BASE_URL)/mixed" | python3 -m json.tool || \
		(echo "❌ /mixed failed" && exit 1)
	@echo "✅ /mixed OK"

## test-metrics: Verify Prometheus metrics endpoint
test-metrics:
	@echo "🔍 Testing /metrics (Prometheus format) ..."
	@curl -sf "$(BASE_URL):8080/metrics" | head -20
	@echo ""
	@echo "✅ /metrics OK"

## test-all: Run all smoke tests
test-all: test-health test-users test-compute test-mixed test-metrics
	@echo ""
	@echo "✅ All smoke tests passed"

# -----------------------------------------------------------
# Load Tests — Requires k6 installed
# Install: https://grafana.com/docs/k6/latest/set-up/install-k6/
#   Linux ARM64: sudo apt install k6 (after adding k6 apt repo)
#   Or Docker:   docker run --rm -i grafana/k6 run - <loadtest/baseline.js
# -----------------------------------------------------------

## load-baseline: Steady-state 5000 req/s for 5 minutes
load-baseline:
	@echo "🚀 Running baseline load test (5000 req/s, 5m) ..."
	@echo "   Grafana:    http://localhost:3000"
	@echo "   Prometheus: http://localhost:9090"
	@echo ""
	@mkdir -p results
	$(K6) run --out json=results/baseline.json loadtest/baseline.js
	@echo "✅ Baseline complete. Results in results/baseline.json"

## load-stress: Ramp from 1000 to 20000 req/s over 10 minutes
load-stress:
	@echo "🚀 Running stress test (1K→20K req/s ramp, ~12m) ..."
	@mkdir -p results
	$(K6) run --out json=results/stress.json loadtest/stress.js
	@echo "✅ Stress test complete. Results in results/stress.json"

## load-spike: 3x spike bursts at 15000 req/s
load-spike:
	@echo "🚀 Running spike test (3x 30s bursts at 15K req/s) ..."
	@mkdir -p results
	$(K6) run --out json=results/spike.json loadtest/spike.js
	@echo "✅ Spike test complete. Results in results/spike.json"

# -----------------------------------------------------------
# Validation — Pre-merge checks
# -----------------------------------------------------------

## validate: Validate docker-compose and prometheus config syntax
validate:
	@echo "🔍 Validating docker-compose.yml ..."
	$(COMPOSE) config --quiet && echo "✅ docker-compose.yml OK"
	@echo ""
	@echo "🔍 Validating prometheus.yml ..."
	@docker run --rm --entrypoint promtool \
		-v $$(pwd)/config/prometheus:/etc/prometheus \
		prom/prometheus:v2.52.0 \
		check config /etc/prometheus/prometheus.yml && \
		echo "✅ prometheus.yml OK"
	@echo ""
	@echo "🔍 Validating k6 scripts ..."
	@$(K6) inspect loadtest/baseline.js > /dev/null && echo "✅ baseline.js OK"
	@$(K6) inspect loadtest/stress.js   > /dev/null && echo "✅ stress.js OK"
	@$(K6) inspect loadtest/spike.js    > /dev/null && echo "✅ spike.js OK"

# -----------------------------------------------------------
# Cleanup
# -----------------------------------------------------------

## clean: Remove containers and anonymous volumes (keeps named volumes)
clean:
	$(COMPOSE) down --remove-orphans

## nuke: Remove everything including persistent volumes (DESTROYS ALL DATA)
nuke:
	@echo "⚠️  This will delete ALL data including PostgreSQL seed data."
	@echo "    Press Ctrl+C to cancel, or wait 5 seconds to continue..."
	@sleep 5
	$(COMPOSE) down -v --remove-orphans
	@echo "✅ All containers and volumes removed. Re-run 'make up' to start fresh."

# -----------------------------------------------------------
# Help
# -----------------------------------------------------------

## help: Show this help message
help:
	@echo ""
	@echo "Performance Observatory — Available Make Targets"
	@echo "================================================="
	@grep -E '^## ' Makefile | sed 's/## /  make /' | column -t -s ':'
	@echo ""
