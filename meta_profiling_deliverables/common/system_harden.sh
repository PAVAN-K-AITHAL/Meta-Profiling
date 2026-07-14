#!/bin/bash
# =============================================================================
# system_harden.sh — System Hardening for Meta-Profiling Experiments
# =============================================================================
# Run BEFORE any experiment session to ensure reproducible, low-noise measurements.
#
# Usage:
#   sudo bash system_harden.sh          # Apply hardening + record state
#   sudo bash system_harden.sh --check  # Only check current state
#   sudo bash system_harden.sh --revert # Revert all changes
#
# Requires: root privileges
# =============================================================================

set -euo pipefail

RESULTS_BASE="../results"
SESSION_ID=$(date +%Y%m%d_%H%M%S)
SESSION_DIR="${RESULTS_BASE}/session_${SESSION_ID}"
NON_ISOLATED="0-5,8-13,16-21,24-29"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✅ $1${NC}"; }
warn() { echo -e "  ${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "  ${RED}❌ $1${NC}"; }

# ---------------------------------------------------------------------------
check_state() {
    echo "============================================="
    echo "  System State Check"
    echo "============================================="

    # Turbo Boost
    if [ -f /sys/devices/system/cpu/intel_pstate/no_turbo ]; then
        val=$(cat /sys/devices/system/cpu/intel_pstate/no_turbo)
        [ "$val" = "1" ] && ok "Turbo Boost: DISABLED" || fail "Turbo Boost: ENABLED"
    else
        warn "Turbo Boost: intel_pstate not found"
    fi

    # ASLR
    val=$(cat /proc/sys/kernel/randomize_va_space)
    [ "$val" = "0" ] && ok "ASLR: DISABLED" || fail "ASLR: ENABLED (value=$val)"

    # THP
    thp=$(cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo "unknown")
    echo "$thp" | grep -q '\[never\]' && ok "THP: DISABLED" || fail "THP: ENABLED ($thp)"

    # isolcpus
    cmdline=$(cat /proc/cmdline)
    echo "$cmdline" | grep -q "isolcpus" && \
        ok "isolcpus: $(echo "$cmdline" | grep -oP 'isolcpus=\S+')" || \
        fail "isolcpus: NOT SET"

    # CPU governor
    if [ -f /sys/devices/system/cpu/cpu6/cpufreq/scaling_governor ]; then
        gov=$(cat /sys/devices/system/cpu/cpu6/cpufreq/scaling_governor)
        [ "$gov" = "performance" ] && ok "CPU Governor: performance" || warn "CPU Governor: $gov"
    fi

    # NTP/Chrony
    if systemctl is-active --quiet ntp 2>/dev/null || systemctl is-active --quiet chronyd 2>/dev/null; then
        warn "NTP/Chrony: RUNNING"
    else
        ok "NTP/Chrony: STOPPED"
    fi

    # HT siblings
    echo ""
    echo "  HyperThread Siblings:"
    for cpu in 6 7 14 15; do
        sibling=$(cat /sys/devices/system/cpu/cpu${cpu}/topology/thread_siblings_list 2>/dev/null || echo "?")
        echo "    CPU $cpu → siblings: $sibling"
    done

    # lmbench check
    echo ""
    if command -v lat_mem_rd &>/dev/null; then
        ok "lmbench: lat_mem_rd found at $(which lat_mem_rd)"
    elif [ -f /usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd ]; then
        ok "lmbench: lat_mem_rd found at /usr/lib/lmbench/bin/x86_64-linux-gnu/lat_mem_rd"
    else
        fail "lmbench: lat_mem_rd NOT found — install with: sudo apt install lmbench"
    fi

    echo "============================================="
}

# ---------------------------------------------------------------------------
apply_hardening() {
    echo "============================================="
    echo "  Applying System Hardening"
    echo "============================================="

    # Disable Turbo Boost
    [ -f /sys/devices/system/cpu/intel_pstate/no_turbo ] && \
        echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo && ok "Turbo Boost disabled" || \
        warn "Cannot disable Turbo Boost"

    # Disable ASLR
    echo 0 > /proc/sys/kernel/randomize_va_space
    ok "ASLR disabled"

    # Disable THP
    echo never > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
    echo never > /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
    ok "THP disabled"

    # CPU governor → performance
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo performance > "$cpu" 2>/dev/null || true
    done
    ok "CPU governor set to performance"

    # Move IRQs off isolated cores
    for irq_dir in /proc/irq/[0-9]*/; do
        [ -f "${irq_dir}smp_affinity_list" ] && \
            echo "$NON_ISOLATED" > "${irq_dir}smp_affinity_list" 2>/dev/null || true
    done
    ok "IRQs moved off isolated cores"

    # Stop NTP
    systemctl stop ntp 2>/dev/null || true
    systemctl stop chronyd 2>/dev/null || true
    ok "NTP/Chrony stopped"

    # Drop caches
    sync && echo 3 > /proc/sys/vm/drop_caches
    ok "Kernel caches dropped"

    echo "============================================="
    echo ""
    record_state
}

# ---------------------------------------------------------------------------
record_state() {
    mkdir -p "$SESSION_DIR"
    echo "  Recording system state → $SESSION_DIR"

    lscpu > "${SESSION_DIR}/lscpu.txt" 2>&1
    uname -a > "${SESSION_DIR}/uname.txt" 2>&1
    cat /proc/cmdline > "${SESSION_DIR}/cmdline.txt" 2>&1
    dmesg | tail -100 > "${SESSION_DIR}/dmesg_tail.txt" 2>&1
    numactl --hardware > "${SESSION_DIR}/numa.txt" 2>&1 || echo "numactl not available" > "${SESSION_DIR}/numa.txt"
    cat /sys/devices/system/cpu/intel_pstate/no_turbo > "${SESSION_DIR}/turbo.txt" 2>&1 || true
    cat /proc/sys/kernel/randomize_va_space > "${SESSION_DIR}/aslr.txt" 2>&1
    cat /sys/kernel/mm/transparent_hugepage/enabled > "${SESSION_DIR}/thp.txt" 2>&1 || true

    for cpu in 6 7 14 15; do
        echo "CPU $cpu: siblings=$(cat /sys/devices/system/cpu/cpu${cpu}/topology/thread_siblings_list 2>/dev/null), L3=$(cat /sys/devices/system/cpu/cpu${cpu}/cache/index3/shared_cpu_list 2>/dev/null)" >> "${SESSION_DIR}/topology.txt"
    done

    git log -1 --format="%H %s" > "${SESSION_DIR}/git_commit.txt" 2>&1 || true
    ok "System state recorded (session: ${SESSION_ID})"
}

# ---------------------------------------------------------------------------
revert_hardening() {
    echo "  Reverting system hardening..."
    [ -f /sys/devices/system/cpu/intel_pstate/no_turbo ] && echo 0 > /sys/devices/system/cpu/intel_pstate/no_turbo && ok "Turbo Boost re-enabled"
    echo 2 > /proc/sys/kernel/randomize_va_space && ok "ASLR re-enabled"
    echo always > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true && ok "THP re-enabled"
    systemctl start ntp 2>/dev/null || systemctl start chronyd 2>/dev/null || true && ok "NTP restarted"
}

# ---------------------------------------------------------------------------
[ "$(id -u)" -ne 0 ] && echo "ERROR: Run as root (sudo)." && exit 1

case "${1:-}" in
    --check)  check_state ;;
    --revert) revert_hardening ;;
    *)        apply_hardening; check_state ;;
esac
