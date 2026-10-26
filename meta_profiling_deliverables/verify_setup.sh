#!/usr/bin/env bash
# verify_setup.sh — Task 9: verify the machine and the perf-stat pipeline
#
# Part A: environment checks (isolation, PMU, tools) — no data collected
# Part B: Task 9 pilot — ruler test, H1 pilot run, CSV checks, analysis,
#         figures. Output goes to results/verify_<timestamp>/
#
# Usage:
#   sudo bash verify_setup.sh                # checks + H1 pilot (50 iterations, ~15 min)
#   sudo bash verify_setup.sh --quick        # checks + H1 pilot (5 iterations, ~2 min)
#   sudo bash verify_setup.sh --checks-only  # Part A only
#
# Exit status: 0 if no FAIL, 1 otherwise. WARNs do not fail the run but
# should be resolved before the full experiment.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKLOAD_CPU=6
PERF_RECORD_CPU=7
RULER_CPU=14
EVENTS="cycles,L1-dcache-load-misses,branch-misses,context-switches,page-faults"
ITERATIONS=50
CHECKS_ONLY=0

for arg in "$@"; do
    case $arg in
        --quick)       ITERATIONS=5 ;;
        --checks-only) CHECKS_ONLY=1 ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

PASS=0; WARN=0; FAIL=0
pass() { echo "  [PASS] $*"; PASS=$((PASS + 1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN + 1)); }
fail() { echo "  [FAIL] $*"; FAIL=$((FAIL + 1)); }
section() { echo; echo "=== $* ==="; }

if [ "${EUID}" -ne 0 ]; then
    echo "Run as root: sudo bash $0 $*" >&2
    exit 1
fi

# sudo resets PATH; hey usually lives in the invoking user's ~/go/bin
if [ -n "${SUDO_USER:-}" ]; then
    SUDO_HOME="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
    [ -d "${SUDO_HOME}/go/bin" ] && export PATH="${SUDO_HOME}/go/bin:${PATH}"
fi

# Expand an isolcpus-style list ("6,7,14-15") to one CPU per line
expand_cpus() {
    tr ',' '\n' <<< "$1" | while read -r r; do
        [ -z "$r" ] && continue
        if [[ "$r" == *-* ]]; then seq "${r%-*}" "${r#*-}"; else echo "$r"; fi
    done
}

# ===========================================================================
# Part A: environment
# ===========================================================================

section "CPU topology"
MODEL="$(lscpu | sed -n 's/^Model name:[[:space:]]*//p')"
NCPU="$(nproc --all)"
if [[ "${MODEL}" == *"E5-2620 v4"* ]]; then pass "CPU: ${MODEL}"; else warn "CPU is '${MODEL}' (scripts assume Xeon E5-2620 v4)"; fi
if [ "${NCPU}" -ge 32 ]; then pass "${NCPU} logical CPUs"; else fail "${NCPU} logical CPUs (need CPU ${RULER_CPU} and HT siblings up to 31)"; fi

for cpu in ${WORKLOAD_CPU} ${PERF_RECORD_CPU} ${RULER_CPU}; do
    sib="$(cat /sys/devices/system/cpu/cpu${cpu}/topology/thread_siblings_list 2>/dev/null)"
    node="$(basename "$(ls -d /sys/devices/system/cpu/cpu${cpu}/node* 2>/dev/null | head -1)" 2>/dev/null)"
    echo "         CPU ${cpu}: HT siblings=${sib:-?}  ${node:-node?}"
done
NODE6="$(ls -d /sys/devices/system/cpu/cpu${WORKLOAD_CPU}/node* 2>/dev/null | head -1)"
NODE14="$(ls -d /sys/devices/system/cpu/cpu${RULER_CPU}/node* 2>/dev/null | head -1)"
if [ -n "${NODE6}" ] && [ "$(basename "${NODE6}")" != "$(basename "${NODE14}")" ]; then
    pass "ruler CPU ${RULER_CPU} is on a different NUMA node than workload CPU ${WORKLOAD_CPU}"
else
    warn "could not confirm CPU ${RULER_CPU} is on the other socket"
fi

section "CPU isolation"
ISOL="$(grep -o 'isolcpus=[^ ]*' /proc/cmdline | cut -d= -f2 | sed 's/^[a-z_,]*,\([0-9]\)/\1/')"
if [ -z "${ISOL}" ]; then
    fail "isolcpus not set in /proc/cmdline"
else
    ISOL_LIST="$(expand_cpus "${ISOL}")"
    for cpu in ${WORKLOAD_CPU} ${PERF_RECORD_CPU} ${RULER_CPU}; do
        if grep -qx "${cpu}" <<< "${ISOL_LIST}"; then pass "CPU ${cpu} isolated"; else fail "CPU ${cpu} NOT isolated (isolcpus=${ISOL})"; fi
        for sib in $(expand_cpus "$(cat /sys/devices/system/cpu/cpu${cpu}/topology/thread_siblings_list)"); do
            [ "${sib}" = "${cpu}" ] && continue
            if grep -qx "${sib}" <<< "${ISOL_LIST}"; then
                pass "HT sibling ${sib} of CPU ${cpu} isolated"
            else
                warn "HT sibling ${sib} of CPU ${cpu} NOT isolated — Task 10 (GRUB + reboot) pending"
            fi
        done
    done
fi
# Anything other than our own processes running on the isolated CPUs?
for cpu in ${WORKLOAD_CPU} ${PERF_RECORD_CPU} ${RULER_CPU}; do
    n="$(ps -eLo psr,comm --no-headers | awk -v c="${cpu}" '$1==c && $2 !~ /^(kworker|ksoftirqd|migration|cpuhp|rcu|idle_inject|irq|watchdog|swapper)/' | wc -l)"
    if [ "${n}" -eq 0 ]; then pass "no user threads on CPU ${cpu}"; else warn "${n} non-kernel thread(s) on CPU ${cpu}: $(ps -eLo psr,comm --no-headers | awk -v c="${cpu}" '$1==c{print $2}' | sort -u | tr '\n' ' ')"; fi
done

section "PMU / perf"
if command -v perf >/dev/null && perf --version >/dev/null 2>&1; then
    pass "$(perf --version) (kernel $(uname -r))"
else
    fail "perf missing or not matching kernel: sudo apt install linux-tools-common linux-tools-$(uname -r)"
fi
PARANOID="$(cat /proc/sys/kernel/perf_event_paranoid)"
if [ "${PARANOID}" -le -1 ]; then pass "perf_event_paranoid=${PARANOID}"; else warn "perf_event_paranoid=${PARANOID} (pipeline sets -1; or: sysctl -w kernel.perf_event_paranoid=-1)"; fi
WD="$(cat /proc/sys/kernel/nmi_watchdog 2>/dev/null || echo 0)"
if [ "${WD}" = 0 ]; then pass "nmi_watchdog=0 (no PMU counter reserved)"; else warn "nmi_watchdog=${WD} — holds a PMU counter; if counts get multiplexed: sysctl -w kernel.nmi_watchdog=0"; fi

# Real counter test: all events counted, 100% running (no multiplexing)
OUT="$(taskset -c ${RULER_CPU} perf stat -x, -C ${WORKLOAD_CPU} -e ${EVENTS} -- sleep 1 2>&1 >/dev/null)"
for ev in ${EVENTS//,/ }; do
    line="$(grep -E "^[^,]*,[^,]*,${ev}(:[a-zA-Z]+)?," <<< "${OUT}" | head -1)"
    val="$(cut -d, -f1 <<< "${line}")"; pct="$(cut -d, -f5 <<< "${line}")"
    if [ -z "${line}" ] || [[ ! "${val}" =~ ^[0-9]+$ ]]; then
        fail "event ${ev} not counted: ${line:-no output}"
    elif [ -n "${pct}" ] && [ "${pct%.*}" -lt 100 ]; then
        fail "event ${ev} multiplexed (${pct}% running)"
    else
        pass "event ${ev} counted exactly (${val})"
    fi
done
# perf stat -p (the tool_overhead path) on a throwaway process
sleep 5 & SPID=$!
OUT="$(taskset -c ${RULER_CPU} perf stat -x, -p ${SPID} -e cycles,context-switches -- sleep 1 2>&1 >/dev/null)"
kill ${SPID} 2>/dev/null; wait ${SPID} 2>/dev/null
if grep -qE '^[0-9]+,[^,]*,context-switches' <<< "${OUT}"; then pass "perf stat -p <pid> attaches"; else fail "perf stat -p failed: ${OUT}"; fi

section "System hardening"
THP="$(grep -o '\[[a-z]*\]' /sys/kernel/mm/transparent_hugepage/enabled | tr -d '[]')"
if [ "${THP}" = never ]; then pass "THP=never"; else warn "THP=${THP} (run: sudo bash common/system_harden.sh)"; fi
NT="$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo '?')"
if [ "${NT}" = 1 ]; then pass "Turbo Boost disabled"; else warn "Turbo Boost not disabled (no_turbo=${NT}) — adds frequency variance"; fi
GOV="$(cat /sys/devices/system/cpu/cpu${WORKLOAD_CPU}/cpufreq/scaling_governor 2>/dev/null || echo '?')"
if [ "${GOV}" = performance ]; then pass "governor=performance on CPU ${WORKLOAD_CPU}"; else warn "governor=${GOV} on CPU ${WORKLOAD_CPU} (want performance)"; fi

section "Tools & dependencies"
if python3 -c "import pandas, numpy, scipy, matplotlib" 2>/dev/null; then
    pass "root python3 has pandas/numpy/scipy/matplotlib"
else
    fail "root python3 missing deps: sudo apt install -y python3-pandas python3-scipy python3-matplotlib"
fi
if command -v lat_mem_rd >/dev/null || [ -x /usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd ]; then
    pass "lat_mem_rd found"
else
    warn "lat_mem_rd missing (sudo apt install lmbench) — falls back to dummy_workload"
fi
if command -v hey >/dev/null; then pass "hey found ($(command -v hey))"; else warn "hey missing — Go API phase will be skipped (go install github.com/rakyll/hey@latest)"; fi
API_PID="$(docker inspect --format '{{.State.Pid}}' perf-go-api 2>/dev/null || echo "")"
if [ -n "${API_PID}" ] && [ "${API_PID}" != 0 ]; then
    pass "Go API container running (PID ${API_PID})"
    code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost/compute || echo 000)"
    if [ "${code}" = 200 ]; then pass "http://localhost/compute → 200"; else warn "http://localhost/compute → ${code}"; fi
else
    warn "Go API container perf-go-api not running (cd .. && docker compose up -d)"
fi
FREE_MB="$(df -Pm /tmp | awk 'NR==2{print $4}')"
if [ "${FREE_MB}" -gt 2048 ]; then pass "/tmp free: ${FREE_MB} MB"; else warn "/tmp free: ${FREE_MB} MB (perf record writes there)"; fi

if [ "${CHECKS_ONLY}" = 1 ] || [ "${FAIL}" -gt 0 ]; then
    echo
    [ "${FAIL}" -gt 0 ] && [ "${CHECKS_ONLY}" = 0 ] && echo "  Skipping Task 9 pilot: fix the FAILs above first."
    echo "  Summary: ${PASS} PASS, ${WARN} WARN, ${FAIL} FAIL"
    [ "${FAIL}" -eq 0 ]; exit $?
fi

# ===========================================================================
# Part B: Task 9 pilot
# ===========================================================================

OUTDIR="${SCRIPT_DIR}/results/verify_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${OUTDIR}"
CSV="${OUTDIR}/pilot_dummy.csv"

section "Step 3: perf_stat_ruler"
if python3 - "${SCRIPT_DIR}/common" ${WORKLOAD_CPU} ${RULER_CPU} <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from perf_stat_ruler import check_perf_stat_available, measure_cpu_perf_stat
ok = check_perf_stat_available(ruler_cpu=sys.argv[3], verbose=True)
data = measure_cpu_perf_stat(target_cpu=sys.argv[2], duration=2, ruler_cpu=sys.argv[3])
print("  Result:", data)
sys.exit(0 if ok and data and len(data) == 5 else 1)
EOF
then pass "ruler returns all 5 metrics"; else fail "ruler test failed"; fi

section "Step 4: H1 pilot (${ITERATIONS} iterations)"
(cd "${SCRIPT_DIR}/dummy_workload_test" && \
    python3 run_hypotheses_dummy.py --iterations "${ITERATIONS}" \
        --hypothesis H1 --output "${CSV}") 2>&1 | tee "${OUTDIR}/pilot.log" | grep -vE '^\s*H1 iter'
if [ -s "${CSV}" ]; then pass "pilot CSV written: ${CSV}"; else fail "pilot produced no CSV (see ${OUTDIR}/pilot.log)"; fi

section "Step 5: CSV checks"
if [ -s "${CSV}" ] && python3 - "${CSV}" "${ITERATIONS}" <<'EOF'
import sys
import pandas as pd
df = pd.read_csv(sys.argv[1]); n = int(sys.argv[2]); bad = 0
def report(ok, msg):
    global bad
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}"); bad += 0 if ok else 1
print(df.groupby('run_type')[['cycles', 'cache_misses', 'branch_misses',
                              'context_switches', 'page_faults']].describe().T.to_string())
tool = df[df.run_type == 'tool_overhead']; wl = df[df.run_type == 'workload_baseline']
report(len(tool) >= 0.9 * n, f"tool_overhead rows: {len(tool)}/{n}")
report(len(wl) >= 0.9 * n, f"workload_baseline rows: {len(wl)}/{n}")
cols = ['cycles', 'cache_misses', 'branch_misses', 'context_switches', 'page_faults']
report(not df[cols].isna().any().any(), "no empty metric cells")
report((df.cycles > 0).all(), "all cycles > 0")
# Workload CPU should be fully busy: cycles / 5 s ≈ clock frequency
ghz = wl.cycles.mean() / 5 / 1e9
report(1.0 < ghz < 3.5, f"workload CPU effective clock {ghz:.2f} GHz (1.0–3.5 expected)")
ratio = tool.cycles.mean() / wl.cycles.mean() * 100
report(0 < ratio < 50, f"perf record / workload cycles = {ratio:.3f}%")
cv = tool.cycles.std() / tool.cycles.mean() * 100
print(f"  [INFO] tool_overhead cycles CV = {cv:.1f}%")
sys.exit(1 if bad else 0)
EOF
then pass "CSV content plausible"; else fail "CSV checks failed"; fi

section "Step 6: analysis"
if python3 "${SCRIPT_DIR}/common/analyze_hypotheses.py" "${CSV}" \
        --output "${OUTDIR}/analysis_report.md" > "${OUTDIR}/analysis.log" 2>&1; then
    pass "report: ${OUTDIR}/analysis_report.md"
    sed -n '/## H4/,/^---/p' "${OUTDIR}/analysis_report.md" | head -20
else
    fail "analysis failed (see ${OUTDIR}/analysis.log)"
fi

section "Step 7: figures"
if python3 "${SCRIPT_DIR}/common/generate_figures.py" "${CSV}" \
        --outdir "${OUTDIR}/figures" --isolcpus "${ISOL}" > "${OUTDIR}/figures.log" 2>&1; then
    # H1-only pilot: Figures 3, 4, 5 (H2/H3 figures are skipped by design)
    N_PDF="$(find "${OUTDIR}/figures" -name '*.pdf' | wc -l)"
    if [ "${N_PDF}" -ge 3 ]; then pass "${N_PDF} PDF figures in ${OUTDIR}/figures"; else fail "only ${N_PDF} PDF figures (see ${OUTDIR}/figures.log)"; fi
else
    fail "figure generation failed (see ${OUTDIR}/figures.log)"
fi

[ -n "${SUDO_USER:-}" ] && chown -R "${SUDO_USER}": "${OUTDIR}"

echo
echo "  Summary: ${PASS} PASS, ${WARN} WARN, ${FAIL} FAIL"
echo "  Output:  ${OUTDIR}"
[ "${FAIL}" -eq 0 ]
