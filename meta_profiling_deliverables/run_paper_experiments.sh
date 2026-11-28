#!/usr/bin/env bash
# run_paper_experiments.sh — experiment campaign for the paper (two-sided method)
#
# Every profiled window is measured from both sides: perf record's own
# process (CPU 7) and the workload it slows down (CPU 6). See
# docs/experiment_plan.md for what each phase answers.
#
# Usage:
#   sudo bash run_paper_experiments.sh check            # pre-flight only (~1 min)
#   sudo bash run_paper_experiments.sh pilot            # 10-iteration smoke test (~5 min)
#   sudo bash run_paper_experiments.sh all              # every phase below (~10 h, run overnight)
#   sudo bash run_paper_experiments.sh h2 h3            # selected phases
#   sudo bash run_paper_experiments.sh --session NAME h1w   # add to / resume an existing session
#
# Phases (default iterations):
#   h1      perf record -g -c 100K on dummy_workload        100 iters   ~35 min
#   xcheck  wall-clock check: fixed work timed with/without perf record   ~10 min
#   h2      sampling-period sweep, 10 periods              50 blocks   ~2 h 10 min
#   h3      call-graph modes on dummy_workload             50 blocks   ~1 h
#   h3deep  call-graph modes on synth_wl depth 64          50 blocks   ~1 h
#   h1w     7 workloads (stack depth × working set)        30 blocks   ~1 h 30 min
#   goapi   Go API: perf record's cost + H4 at -c 20M/500K/100K  100 iters  ~2 h
#   analyze report + figures from everything in the session
#
# A phase whose CSV already exists in the session is skipped, so after a
# crash re-run the same command with --session to continue. CSVs are written
# as *.partial and renamed only when the phase finishes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SESSION="paper_$(date +%Y%m%d_%H%M%S)"
PHASES=()
ITERS_OVERRIDE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --session)      SESSION="$2"; shift 2 ;;
        --iterations=*) ITERS_OVERRIDE="${1#*=}"; shift ;;
        check|pilot|xcheck|h1|h2|h3|h3deep|h1w|goapi|analyze) PHASES+=("$1"); shift ;;
        all)            PHASES+=(xcheck h1 h2 h3 h3deep h1w goapi analyze); shift ;;
        *) echo "Unknown argument: $1" >&2; sed -n 2,26p "$0"; exit 2 ;;
    esac
done
[ ${#PHASES[@]} -eq 0 ] && { sed -n 2,26p "$0"; exit 2; }

if [ "${EUID}" -ne 0 ]; then
    echo "ERROR: run as root: sudo bash $0 $*" >&2
    exit 1
fi

# sudo resets PATH; hey usually lives in the invoking user's ~/go/bin
if [ -n "${SUDO_USER:-}" ]; then
    SUDO_HOME="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
    [ -d "${SUDO_HOME}/go/bin" ] && export PATH="${SUDO_HOME}/go/bin:${PATH}"
fi

OUT="${SCRIPT_DIR}/results/${SESSION}"
mkdir -p "${OUT}"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${OUT}/run.log"; }
iters() { echo "${ITERS_OVERRIDE:-$1}"; }

# ---- System state (once per session) ----
if [ ! -f "${OUT}/system_state.txt" ]; then
    {
        echo "Date: $(date)"
        echo "Kernel: $(uname -r)"
        echo "Git commit: $(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
        echo "Git dirty files: $(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null | wc -l)"
        echo "perf: $(perf --version 2>/dev/null)"
        echo "cmdline: $(cat /proc/cmdline)"
        for k in perf_event_paranoid nmi_watchdog perf_event_max_sample_rate \
                 perf_cpu_time_max_percent perf_event_max_stack; do
            echo "kernel.$k = $(cat /proc/sys/kernel/$k 2>/dev/null || echo n/a)"
        done
        echo "THP: $(cat /sys/kernel/mm/transparent_hugepage/enabled)"
        echo "no_turbo: $(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo n/a)"
        echo "ASLR: $(cat /proc/sys/kernel/randomize_va_space)"
        echo; lscpu
    } > "${OUT}/system_state.txt" 2>&1
fi

# ---- Pre-flight (every invocation) ----
preflight() {
    log "Pre-flight checks"
    grep -q isolcpus /proc/cmdline || { log "ERROR: isolcpus not set (see run_full_experiments.sh)"; exit 1; }
    command -v perf >/dev/null || { log "ERROR: perf not installed"; exit 1; }
    command -v gcc >/dev/null || { log "ERROR: gcc not installed"; exit 1; }
    command -v bc >/dev/null || { log "ERROR: bc not installed (sudo apt install bc)"; exit 1; }
    python3 -c "import pandas, numpy, scipy, matplotlib" 2>/dev/null || {
        log "ERROR: root's python3 lacks pandas/numpy/scipy/matplotlib:"
        log "  sudo apt install -y python3-pandas python3-scipy python3-matplotlib"; exit 1; }
    sysctl -q -w kernel.perf_event_paranoid=-1
    # The NMI watchdog holds a PMU counter; the profiled CPU 6 measurement
    # needs all four general-purpose counters. Restored on reboot.
    if [ "$(cat /proc/sys/kernel/nmi_watchdog)" != 0 ]; then
        sysctl -q -w kernel.nmi_watchdog=0
        log "Set kernel.nmi_watchdog=0 (PMU counter budget)"
    fi
    if [ -f /sys/devices/system/cpu/intel_pstate/no_turbo ] && \
       [ "$(cat /sys/devices/system/cpu/intel_pstate/no_turbo)" != 1 ]; then
        log "WARNING: Turbo Boost is on — run: sudo bash common/system_harden.sh"
    fi
    df_free=$(df --output=avail -BG /tmp | tail -1 | tr -dc 0-9)
    [ "${df_free}" -lt 5 ] && log "WARNING: only ${df_free} GB free in /tmp; DWARF runs write ~1 GB perf.data files"
    log "Pre-flight OK"
}

# Run one dummy-runner phase into <name>.csv unless it already exists
run_dummy_phase() {
    local name="$1"; shift
    local dest="${DEST:-${OUT}}"
    mkdir -p "${dest}"
    local csv="${dest}/${name}.csv"
    if [ -f "${csv}" ]; then
        log "Phase ${name}: ${csv} exists — skipping"
        return
    fi
    log "Phase ${name}: starting ($*)"
    (cd "${SCRIPT_DIR}/dummy_workload_test" && \
        python3 run_hypotheses_dummy.py "$@" --output "${csv}.partial") 2>&1 | tee -a "${dest}/${name}.log"
    mv "${csv}.partial" "${csv}"
    log "Phase ${name}: done — $(($(wc -l < "${csv}") - 1)) rows"
}

run_goapi_phase() {
    local csv="${OUT}/goapi.csv"
    if [ -f "${csv}" ]; then
        log "Phase goapi: ${csv} exists — skipping"
        return
    fi
    command -v hey >/dev/null || { log "ERROR: hey not installed (go install github.com/rakyll/hey@latest)"; return 1; }
    local api_pid
    api_pid=$(docker inspect --format '{{.State.Pid}}' perf-go-api 2>/dev/null || echo "")
    if [ -z "${api_pid}" ] || [ "${api_pid}" = 0 ]; then
        log "Starting the Go API stack (docker compose up -d)"
        (cd "${REPO_ROOT}" && docker compose up -d) && sleep 15
    fi
    log "Phase goapi: starting"
    (cd "${SCRIPT_DIR}/go_api_test" && \
        python3 run_hypotheses_goapi.py --iterations "$(iters 100)" \
            --periods 20000000,500000,100000 --output "${csv}.partial") 2>&1 | tee -a "${OUT}/goapi.log"
    mv "${csv}.partial" "${csv}"
    log "Phase goapi: done — $(($(wc -l < "${csv}") - 1)) rows"
}

# Wall-clock cross-check, independent of every PMU counter: synth_wl does a
# fixed amount of work (depth 3, 1 MB) and is timed with no profiler and with
# perf record -g at each period, in alternating order.
run_xcheck_phase() {
    local csv="${OUT}/xcheck.csv"
    if [ -f "${csv}" ]; then
        log "Phase xcheck: ${csv} exists — skipping"
        return
    fi
    local dir="${SCRIPT_DIR}/dummy_workload_test" bin
    bin="${dir}/synth_wl"
    gcc -O2 -fno-omit-frame-pointer -fno-optimize-sibling-calls "${dir}/synth_wl.c" -o "${bin}"
    pkill -9 -x dummy_workload 2>/dev/null || true
    pkill -9 -x synth_wl 2>/dev/null || true

    run_once() {  # prints elapsed seconds of one fixed-work run on CPU 6
        local t0 t1
        t0=$(date +%s%N)
        taskset -c 6 "${bin}" 3 1 "$1" >/dev/null
        t1=$(date +%s%N)
        echo "scale=6; (${t1} - ${t0}) / 1000000000" | bc
    }
    # Calibrate to ~5 s with no profiler
    local probe steps
    probe=$(run_once 20000)
    steps=$(echo "20000 * 5 / ${probe}" | bc)
    log "Phase xcheck: ${steps} steps ≈ 5 s per run"

    echo "rep,config_c,seconds,lost_events,throttle_events" > "${csv}.partial"
    local rep c secs q
    for rep in $(seq 1 10); do
        for c in $(printf '%s\n' 0 100000 500000 20000000 | shuf); do
            if [ "${c}" = 0 ]; then
                secs=$(run_once "${steps}")
                echo "${rep},0,${secs},," >> "${csv}.partial"
            else
                taskset -c 7 perf record -g -e cycles -c "${c}" -C 6 -o /tmp/perf-xcheck.data \
                    >/dev/null 2>&1 &
                sleep 1
                secs=$(run_once "${steps}")
                pkill -INT -x perf || true
                while pgrep -x perf >/dev/null; do sleep 0.2; done
                q=$(perf report -i /tmp/perf-xcheck.data --stats 2>/dev/null | awk '
                    /LOST(_SAMPLES)? events:/ {l += $3} /THROTTLE events:/ {t += $3}
                    END {printf "%d,%d", l, t}')
                echo "${rep},${c},${secs},${q}" >> "${csv}.partial"
            fi
            sleep 2
        done
        log "Phase xcheck: rep ${rep}/10"
    done
    mv "${csv}.partial" "${csv}"
    log "Phase xcheck: done"
}

analyze() {
    local dir="${1:-${OUT}}"
    log "Analysis of ${dir}"
    local args=() goapi=""
    for f in "${dir}"/*.csv; do
        [ -e "$f" ] || continue
        case "$(basename "$f")" in
            goapi.csv)  goapi="$f" ;;
            xcheck.csv) args+=(--xcheck "$f") ;;
            *)          args+=("$f") ;;
        esac
    done
    [ -n "${goapi}" ] && args+=(--goapi "${goapi}")
    if [ ${#args[@]} -eq 0 ]; then
        log "No CSVs in ${dir} yet"; return
    fi
    python3 "${SCRIPT_DIR}/common/analyze_v2.py" "${args[@]}" \
        --output "${dir}/report.md" --figures "${dir}/figures"
    log "Report: ${dir}/report.md"
}

log "Session ${SESSION} — phases: ${PHASES[*]}"
preflight

for phase in "${PHASES[@]}"; do
    case "${phase}" in
        check) ;;
        pilot)
            # Kept in pilot/ so it never mixes with the real data
            DEST="${OUT}/pilot" run_dummy_phase pilot_h1 --hypothesis H1 --iterations 10
            DEST="${OUT}/pilot" run_dummy_phase pilot_h2 --hypothesis H2 --iterations 2
            analyze "${OUT}/pilot"
            ;;
        h1)     run_dummy_phase h1 --hypothesis H1 --iterations "$(iters 100)" ;;
        h2)     run_dummy_phase h2 --hypothesis H2 --iterations "$(iters 50)" ;;
        h3)     run_dummy_phase h3 --hypothesis H3 --iterations "$(iters 50)" ;;
        h3deep) run_dummy_phase h3deep --hypothesis H3 --workload synth --depth 64 \
                    --size-mb 1 --iterations "$(iters 50)" ;;
        h1w)    run_dummy_phase h1w --hypothesis H1W --iterations "$(iters 30)" ;;
        goapi)  run_goapi_phase ;;
        xcheck) run_xcheck_phase ;;
        analyze) analyze ;;
    esac
done

# Results belong to the invoking user, not root
[ -n "${SUDO_USER:-}" ] && chown -R "${SUDO_USER}": "${OUT}"
log "Finished. Session directory: ${OUT}"
log "Resume or add phases with: sudo bash $0 --session ${SESSION} <phase>"
