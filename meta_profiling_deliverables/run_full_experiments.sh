#!/usr/bin/env bash
# run_full_experiments.sh — Complete Experiment Pipeline
#
# Runs all experiments required for publication-quality results:
#   Phase 1: Full dummy workload (H1, H2, H3)
#   Phase 2: Go API experiments (H1, H4)
#   Phase 3: Statistical analysis
#   Phase 4: Figure generation
#
# Usage:
#   sudo bash run_full_experiments.sh                  # full run (500 iterations)
#   sudo bash run_full_experiments.sh --pilot          # pilot (50 iterations)
#   sudo bash run_full_experiments.sh --dummy-only     # only H1/H2/H3
#   sudo bash run_full_experiments.sh --goapi-only     # only H1/H4 Go API
#   sudo bash run_full_experiments.sh --iterations=200
#
# Prerequisites:
#   - perf installed: sudo apt install -y linux-tools-common linux-tools-generic
#   - lmbench installed: sudo apt install -y lmbench
#   - Python deps visible to ROOT's python3 (pip --user installs are not):
#       sudo apt install -y python3-pandas python3-scipy python3-matplotlib
#   - hey (load tester): go install github.com/rakyll/hey@latest
#   - Docker + Go API running: cd .. && docker compose up -d
#   - CPUs isolated: isolcpus=6,7,14,15,22,23,30,31 in /proc/cmdline
#   - THP disabled: sudo bash common/system_harden.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
DUMMY_ONLY=0
GOAPI_ONLY=0
ITERATIONS=500

# ---- Parse args ----
for arg in "$@"; do
    case $arg in
        --pilot)        ITERATIONS=50 ;;
        --dummy-only)   DUMMY_ONLY=1 ;;
        --goapi-only)   GOAPI_ONLY=1 ;;
        --iterations=*) ITERATIONS="${arg#*=}" ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if [ "${EUID}" -ne 0 ]; then
    echo "ERROR: run as root: sudo bash $0 $*" >&2
    exit 1
fi

# sudo resets PATH; hey usually lives in the invoking user's ~/go/bin
if [ -n "${SUDO_USER:-}" ]; then
    SUDO_HOME="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
    [ -d "${SUDO_HOME}/go/bin" ] && export PATH="${SUDO_HOME}/go/bin:${PATH}"
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SESSION_DIR="${RESULTS_DIR}/session_${TIMESTAMP}"
mkdir -p "${SESSION_DIR}"

DUMMY_CSV="${SESSION_DIR}/hypotheses_dummy_${TIMESTAMP}.csv"
GOAPI_CSV="${SESSION_DIR}/hypotheses_goapi_${TIMESTAMP}.csv"
REPORT="${SESSION_DIR}/analysis_report.md"
FIGURES_DIR="${SESSION_DIR}/figures"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${SESSION_DIR}/run.log"
}

# ---- Record system state ----
log "Recording system state..."
{
    echo "=== SYSTEM STATE ==="
    echo "Date: $(date)"
    echo "Kernel: $(uname -r)"
    echo "Git commit: $(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    echo "Git dirty: $(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null | wc -l) files"
    echo "perf: $(perf --version 2>/dev/null || echo 'not found')"
    echo ""
    echo "=== /proc/cmdline ==="
    cat /proc/cmdline
    echo ""
    echo "=== isolcpus check ==="
    grep -o 'isolcpus=[^ ]*' /proc/cmdline || echo "isolcpus NOT found in cmdline!"
    echo ""
    echo "=== perf_event_paranoid ==="
    cat /proc/sys/kernel/perf_event_paranoid
    echo ""
    echo "=== nmi_watchdog (occupies a PMU counter when 1) ==="
    cat /proc/sys/kernel/nmi_watchdog 2>/dev/null || echo "n/a"
    echo ""
    echo "=== THP status ==="
    cat /sys/kernel/mm/transparent_hugepage/enabled
    echo ""
    echo "=== Turbo Boost status ==="
    cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo "no_turbo file not found"
    echo ""
    echo "=== numactl --hardware ==="
    numactl --hardware 2>/dev/null || echo "numactl not available"
    echo ""
    echo "=== lscpu ==="
    lscpu
} > "${SESSION_DIR}/system_state.txt" 2>&1
log "System state saved to ${SESSION_DIR}/system_state.txt"

# ---- Pre-flight checks (fail now, not after hours of collection) ----
log "Running pre-flight checks..."

PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid)
if [ "${PARANOID}" -gt -1 ]; then
    log "perf_event_paranoid=${PARANOID}. Setting to -1..."
    sysctl -w kernel.perf_event_paranoid=-1
fi

if ! grep -q 'isolcpus' /proc/cmdline; then
    log "ERROR: isolcpus not in /proc/cmdline. Run:"
    log "  sudo sed -i 's/GRUB_CMDLINE_LINUX=\"/GRUB_CMDLINE_LINUX=\"isolcpus=6,7,14,15,22,23,30,31 /' /etc/default/grub"
    log "  sudo update-grub && sudo reboot"
    exit 1
fi

if ! command -v perf &>/dev/null; then
    log "ERROR: perf not found. Install: sudo apt install linux-tools-common linux-tools-generic"
    exit 1
fi

if ! python3 -c "import pandas, numpy, scipy, matplotlib" 2>/dev/null; then
    log "ERROR: root's python3 cannot import pandas/numpy/scipy/matplotlib."
    log "  Install: sudo apt install -y python3-pandas python3-scipy python3-matplotlib"
    exit 1
fi

if ! command -v lat_mem_rd &>/dev/null && \
   ! [ -f /usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd ]; then
    log "WARNING: lat_mem_rd not found. Install: sudo apt install lmbench"
    log "  Dummy workload will fall back to dummy_workload binary."
fi

log "Pre-flight checks complete."

# ---- Phase 1: Dummy Workload ----
if [ "${GOAPI_ONLY}" -eq 0 ]; then
    log "Starting Phase 1: Dummy Workload Experiments (H1, H2, H3)"
    log "  Iterations: ${ITERATIONS}"
    log "  Output: ${DUMMY_CSV}"

    # cd so the dummy_workload fallback (./dummy_workload) resolves
    (cd "${SCRIPT_DIR}/dummy_workload_test" && \
        python3 run_hypotheses_dummy.py \
            --iterations "${ITERATIONS}" \
            --output "${DUMMY_CSV}" \
            --hypothesis all)

    log "Phase 1 complete. CSV: ${DUMMY_CSV}"
fi

# ---- Phase 2: Go API ----
if [ "${DUMMY_ONLY}" -eq 0 ]; then
    log "Starting Phase 2: Go API Experiments (H1, H4)"

    API_PID=$(docker inspect --format '{{.State.Pid}}' perf-go-api 2>/dev/null || echo "")
    if [ -z "${API_PID}" ] || [ "${API_PID}" = "0" ]; then
        log "WARNING: Go API container not running. Attempting to start..."
        (cd "${REPO_ROOT}" && docker compose up -d)
        sleep 10
        API_PID=$(docker inspect --format '{{.State.Pid}}' perf-go-api 2>/dev/null || echo "")
    fi

    if [ -z "${API_PID}" ] || [ "${API_PID}" = "0" ]; then
        log "ERROR: Could not start Go API. Skipping Phase 2."
    elif ! command -v hey &>/dev/null; then
        log "ERROR: 'hey' not installed. Skipping Phase 2."
        log "  Install: go install github.com/rakyll/hey@latest"
    else
        log "  Go API PID: ${API_PID}"
        log "  Iterations: ${ITERATIONS}"
        log "  Output: ${GOAPI_CSV}"

        (cd "${SCRIPT_DIR}/go_api_test" && \
            python3 run_hypotheses_goapi.py \
                --iterations "${ITERATIONS}" \
                --output "${GOAPI_CSV}" \
                --hypothesis all)

        log "Phase 2 complete. CSV: ${GOAPI_CSV}"
    fi
fi

# ---- Phase 3: Statistical Analysis ----
log "Starting Phase 3: Statistical Analysis"

GOAPI_ARGS=()
if [ -f "${GOAPI_CSV}" ]; then
    GOAPI_ARGS=(--goapi "${GOAPI_CSV}")
fi

if [ -f "${DUMMY_CSV}" ]; then
    python3 "${SCRIPT_DIR}/common/analyze_hypotheses.py" \
        "${DUMMY_CSV}" \
        "${GOAPI_ARGS[@]}" \
        --output "${REPORT}" > /dev/null
    log "Analysis report: ${REPORT}"
else
    log "WARNING: No dummy CSV found — skipping analysis and figures."
fi

# ---- Phase 4: Figure Generation ----
if [ -f "${DUMMY_CSV}" ]; then
    log "Starting Phase 4: Figure Generation"
    python3 "${SCRIPT_DIR}/common/generate_figures.py" \
        "${DUMMY_CSV}" \
        "${GOAPI_ARGS[@]}" \
        --outdir "${FIGURES_DIR}" \
        --isolcpus "$(grep -o 'isolcpus=[^ ]*' /proc/cmdline | cut -d= -f2)"
    log "Figures saved to: ${FIGURES_DIR}"
fi

# Results belong to the invoking user, not root
if [ -n "${SUDO_USER:-}" ]; then
    chown -R "${SUDO_USER}": "${SESSION_DIR}"
fi

# ---- Summary ----
log "=========================================="
log "COMPLETE. Session: ${TIMESTAMP}"
log "Results: ${SESSION_DIR}/"
log "  system_state.txt — recorded system configuration"
if [ -f "${DUMMY_CSV}" ]; then
    log "  $(basename "${DUMMY_CSV}") — $(($(wc -l < "${DUMMY_CSV}") - 1)) data rows"
fi
if [ -f "${GOAPI_CSV}" ]; then
    log "  $(basename "${GOAPI_CSV}") — $(($(wc -l < "${GOAPI_CSV}") - 1)) data rows"
fi
if [ -f "${REPORT}" ]; then
    log "  analysis_report.md — full statistical analysis"
fi
if [ -d "${FIGURES_DIR}" ]; then
    log "  figures/ — $(find "${FIGURES_DIR}" -name '*.pdf' | wc -l) PDF figures"
fi
log "=========================================="
