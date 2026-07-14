# NUMA Topology Analysis: Intel Xeon E5-2620 v4

## Raw Facts From Your Output

| Property | Value |
|----------|-------|
| Total logical CPUs | 32 |
| Sockets | 2 |
| Cores per socket | 8 |
| Threads per core | 2 (HyperThreading ON) |
| Physical cores total | 16 |
| L1d cache | 32 KiB per core (512 KiB / 16 cores) |
| L1i cache | 32 KiB per core |
| L2 cache | 256 KiB per core (4 MiB / 16 cores) |
| L3 cache | **20 MiB per socket** (40 MiB / 2 instances) |
| NUMA Node 0 (Socket 0) | CPUs 0-7, 16-23 |
| NUMA Node 1 (Socket 1) | CPUs 8-15, 24-31 |

---

## Complete CPU Map

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                    SOCKET 0 (NUMA Node 0)                               ║
║                    Shared 20 MiB L3 Cache                               ║
║                                                                         ║
║  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                       ║
║  │ Core 0  │ │ Core 1  │ │ Core 2  │ │ Core 3  │                       ║
║  │ CPU 0   │ │ CPU 1   │ │ CPU 2   │ │ CPU 3   │  ← Thread 0           ║
║  │ CPU 16  │ │ CPU 17  │ │ CPU 18  │ │ CPU 19  │  ← Thread 1 (HT)     ║
║  └─────────┘ └─────────┘ └─────────┘ └─────────┘                       ║
║  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                       ║
║  │ Core 4  │ │ Core 5  │ │ Core 6  │ │ Core 7  │                       ║
║  │ CPU 4   │ │ CPU 5   │ │ CPU 6 🎯│ │ CPU 7 🔬│  ← Thread 0           ║
║  │ CPU 20  │ │ CPU 21  │ │ CPU 22 ⚠│ │ CPU 23 ⚠│  ← Thread 1 (HT)     ║
║  └─────────┘ └─────────┘ └─────────┘ └─────────┘                       ║
╚═══════════════════════════════════════════════════════════════════════════╝

╔═══════════════════════════════════════════════════════════════════════════╗
║                    SOCKET 1 (NUMA Node 1)                               ║
║                    Shared 20 MiB L3 Cache                               ║
║                                                                         ║
║  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                       ║
║  │ Core 0  │ │ Core 1  │ │ Core 2  │ │ Core 3  │                       ║
║  │ CPU 8   │ │ CPU 9   │ │ CPU 10  │ │ CPU 11  │  ← Thread 0           ║
║  │ CPU 24  │ │ CPU 25  │ │ CPU 26  │ │ CPU 27  │  ← Thread 1 (HT)     ║
║  └─────────┘ └─────────┘ └─────────┘ └─────────┘                       ║
║  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                       ║
║  │ Core 4  │ │ Core 5  │ │ Core 6  │ │ Core 7  │                       ║
║  │ CPU 12  │ │ CPU 13  │ │ CPU 14📊│ │ CPU 15  │  ← Thread 0           ║
║  │ CPU 28  │ │ CPU 29  │ │ CPU 30 ⚠│ │ CPU 31  │  ← Thread 1 (HT)     ║
║  └─────────┘ └─────────┘ └─────────┘ └─────────┘                       ║
╚═══════════════════════════════════════════════════════════════════════════╝

Legend:
  🎯 CPU 6  = Target workload (dummy_workload / Go API)     [ISOLATED]
  🔬 CPU 7  = Inner tool (perf record)                      [ISOLATED]
  📊 CPU 14 = Outer ruler (bpftrace / perf stat)            [ISOLATED]
  ⚠  CPU 22, 23, 30 = HyperThread SIBLINGS — NOT ISOLATED!
```

---

## What's Good About Your Current Setup

| Aspect | Assessment | Why |
|--------|-----------|-----|
| CPUs 6 & 7 on different physical cores | ✅ Excellent | They do NOT share L1/L2. Core 6 ≠ Core 7. |
| CPUs 6 & 7 on same socket | ✅ Realistic | They share L3 (20 MiB). In production, a profiler would typically share L3 with its target. Any L3 cache pollution from `perf record` IS part of the real overhead. |
| CPU 14 on different socket | ✅ Excellent | The outer measurement (bpftrace) does NOT share L3 with the target or perf record. It cannot pollute their caches. The ruler is truly independent. |
| Cross-socket memory access for ruler | ✅ Acceptable | CPU 14 accessing perf record's data crosses the QPI interconnect (~100ns extra), but this only affects the ruler's own timing, not the measurement values. |

---

## ⚠️ The Problem: HyperThread Siblings Are NOT Isolated

This is the **one critical issue** the mentor will likely flag.

### Thread Sibling Pairs (from your output)

| Isolated CPU | HyperThread Sibling | Sibling Isolated? |
|-------------|---------------------|-------------------|
| CPU 6 (🎯 target) | **CPU 22** | ❌ **NO** |
| CPU 7 (🔬 perf record) | **CPU 23** | ❌ **NO** |
| CPU 14 (📊 ruler) | **CPU 30** | ❌ **NO** |
| CPU 15 (unused) | **CPU 31** | ❌ **NO** |

### Why This Matters

HyperThread siblings on the same physical core share:
- **L1 data cache** (32 KiB) — fully shared
- **L1 instruction cache** (32 KiB) — fully shared
- **L2 cache** (256 KiB) — fully shared
- **Execution units** (ALUs, FPUs, load/store units) — competitively shared
- **TLB** — competitively shared
- **Branch predictor state** — competitively shared

If CPU 22 is NOT isolated, the OS scheduler can place **any** process on it (e.g., `kworker`, `ksoftirqd`, `rcu_sched`, `jbd2`). That random process will:
1. Evict your dummy_workload's data from L1/L2 cache → **inflates baseline cache-misses**
2. Compete for execution units → **adds noise to baseline cycles**
3. Pollute the branch predictor → **adds noise to baseline branch-misses**

This means your **baseline measurements have more variance** than they should. With n=1000 iterations it averages out, but it weakens your claim of rigorous isolation.

### The Fix

**Option A (Recommended): Isolate the HT siblings too**

```bash
# Update GRUB:
# Old:  isolcpus=6,7,14,15
# New:  isolcpus=6,7,14,15,22,23,30,31
sudo sed -i 's/isolcpus=6,7,14,15/isolcpus=6,7,14,15,22,23,30,31/' /etc/default/grub

# Also update nohz_full and rcu_nocbs to match:
# nohz_full=6,7,14,15,22,23,30,31
# rcu_nocbs=6,7,14,15,22,23,30,31

sudo update-grub
sudo reboot
```

After reboot, verify:
```bash
cat /proc/cmdline | grep isolcpus
# Should show: isolcpus=6,7,14,15,22,23,30,31
```

This leaves CPUs 22, 23, 30, 31 completely idle — no OS process will run on the sibling thread, so your isolated cores have **exclusive access** to all L1/L2 resources.

**Option B: Disable HyperThreading entirely**

```bash
# In BIOS/UEFI: Disable "Intel Hyper-Threading Technology"
# OR at runtime:
echo off | sudo tee /sys/devices/system/cpu/smt/control
```

This is cleaner but halves your total CPU count to 16, which may affect Docker/k6/OS scheduling.

**Option C (Minimum): Acknowledge in the paper**

If re-running experiments is not feasible, add a note:

> "HyperThread siblings of isolated cores (CPUs 22, 23, 30) were not explicitly isolated. While OS processes may occasionally execute on these siblings, the statistical averaging over 1,000 iterations mitigates this source of noise. Future work should isolate all sibling threads."

---

## Answering the Mentor's Question: "Is there a shared L3 or SLC cache?"

### L3 Cache Sharing

From your output:
```
CPU 6 L3 shared_cpu_list: 0-7,16-23    (all of Socket 0)
CPU 7 L3 shared_cpu_list: 0-7,16-23    (all of Socket 0)
```

**Yes, CPUs 6 and 7 share a 20 MiB L3 cache.** This means:
- `perf record` (CPU 7) writing to its ring buffer can evict the target workload's (CPU 6) data from L3
- This L3 cache pollution IS a real component of profiler overhead
- **This is actually what you WANT to measure** — in production, the profiler shares L3 with its target

CPU 14 (Socket 1) has a **separate 20 MiB L3 cache** shared with CPUs 8-15, 24-31. The outer ruler does NOT pollute the target's L3.

### SLC (Snoop Last-Level Cache)

**There is no SLC on this processor.** SLC is a term used in Intel Alder Lake+ (12th gen) consumer CPUs for their ring bus filter. The Xeon E5-2620 v4 (Broadwell-EP) uses a traditional **inclusive L3 cache** connected via a ring interconnect.

The correct term for this system is **LLC (Last Level Cache)** = L3.

Tell the mentor: *"The Xeon E5-2620 v4 has a 20 MiB shared LLC (L3) per socket. There is no SLC — that is an Alder Lake+ consumer feature. CPUs 6 and 7 (target + perf record) share the Socket 0 LLC, which means L3 cache pollution from perf record is captured in our measurements. CPU 14 (outer ruler) is on Socket 1 with a separate LLC, ensuring measurement independence."*

---

## Recommended Diagram for Paper

```
Figure: 3-Layer Isolation Architecture on Intel Xeon E5-2620 v4

                    QPI Interconnect
    ┌──────────────────┐          ┌──────────────────┐
    │   Socket 0       │          │   Socket 1       │
    │  NUMA Node 0     │◄────────►│  NUMA Node 1     │
    │  20 MiB L3 LLC   │          │  20 MiB L3 LLC   │
    │                  │          │                  │
    │  ┌──────────┐    │          │  ┌──────────┐    │
    │  │ Core 6   │    │          │  │ Core 6   │    │
    │  │ CPU 6    │    │          │  │ CPU 14   │    │
    │  │ 🎯Target │    │          │  │ 📊Ruler  │    │
    │  │ L1: 32K  │    │          │  │ L1: 32K  │    │
    │  │ L2: 256K │    │          │  │ L2: 256K │    │
    │  └──────────┘    │          │  └──────────┘    │
    │                  │          │                  │
    │  ┌──────────┐    │          │                  │
    │  │ Core 7   │    │          │                  │
    │  │ CPU 7    │    │          │                  │
    │  │ 🔬perf   │    │          │                  │
    │  │ L1: 32K  │    │          │                  │
    │  │ L2: 256K │    │          │                  │
    │  └──────────┘    │          │                  │
    └──────────────────┘          └──────────────────┘

    ← L3 shared (6↔7) →          ← Separate L3 →
    Cache pollution IS            No cache cross-talk
    part of overhead              with target/tool
```

---

## Summary: What to Tell the Mentor

> **NUMA Topology**: The Xeon E5-2620 v4 is a 2-socket, 16-core (32 logical with HT) system. Each socket has 8 physical cores sharing a 20 MiB L3 LLC.
>
> **CPU Selection Rationale**:
> - CPUs 6 & 7 are on **Socket 0, different physical cores** (Core 6 and Core 7). They share L3 but have independent L1/L2. This intentionally captures L3 cache pollution as a real overhead component.
> - CPU 14 is on **Socket 1** with a completely separate L3 cache, ensuring the outer measurement cannot interfere with the workload or profiler.
>
> **SLC**: Not applicable to this architecture. The E5-2620 v4 uses inclusive L3 LLC.
>
> **Gap identified**: HT siblings (CPUs 22, 23, 30) should also be isolated to prevent L1/L2 resource contention from stray OS processes. We will update `isolcpus` to include these siblings.

---

## Answering the Mentor's Question: "What processes do you bind to these 4 CPUs?"

Here is the exact mapping of processes to CPUs in our 3-layer architecture. We have formalized this into a unified process-to-core binding that will be consistent across all scripts.

### Process-to-Core Binding (3-Layer Isolation Architecture)

| Layer | Process               | CPU(s) | Core  | Socket | Rationale                           |
|-------|-----------------------|--------|-------|--------|-------------------------------------|
| 1     | Target workload       | 6      | C6    | S0     | Isolated from OS scheduler          |
| 2     | `perf record` (inner) | 7      | C7    | S0     | Same socket as target (realistic)   |
| 3     | `perf stat`/`bpftrace`| 14     | C6    | S1     | Different socket → no L3 pollution  |

### Detailed Layout by Experiment

**Experiment A — Dummy Workload (eBPF outer):**
*   **Layer 1 — Target:** `dummy_workload` → **CPU 6** (Core 6, Socket 0)
*   **Layer 2 — Inner tool:** `perf record` → **CPU 7** (Core 7, Socket 0)
*   **Layer 3 — Outer ruler:** `bpftrace` → **CPU 14** (Core 6, Socket 1)

**Experiment B — Go API (eBPF outer):**
*   **Layer 1 — Target:** Go API container → **NOT pinned** (runs on non-isolated CPUs to simulate realistic unpinned microservice)
*   **Layer 2 — Inner tool:** `perf record` → **CPU 7** (Core 7, Socket 0)
*   **Layer 3 — Outer ruler:** `bpftrace` → **CPU 14** (Core 6, Socket 1)

> **Note for consistency**: We are updating all shell scripts and Python runners to strictly adhere to this CPU 6/7/14 pinning scheme to ensure our methodology is watertight for publication.
