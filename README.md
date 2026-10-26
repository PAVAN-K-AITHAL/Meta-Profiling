# Performance Analysis: Meta-Profiling

This repository contains the architecture, target application, and scripts for our **Meta-Profiling (Instrumentation of Instrumentation)** research project.

Our research quantifies the "observer effect" of Linux profiling tools: how much hardware does `perf record` itself consume? We measure it with a three-layer, NUMA-aware isolation setup on an Intel Xeon E5-2620 v4, using `perf stat` (exact PMU counts) as the outer ruler, and test four hypotheses (H1–H4) with formal statistics.

## Repository Structure

- **[`meta_profiling_deliverables/`](meta_profiling_deliverables/)**: the experiment pipeline.
  - [`run_full_experiments.sh`](meta_profiling_deliverables/run_full_experiments.sh): runs everything (data collection → analysis → figures).
  - [`verify_setup.sh`](meta_profiling_deliverables/verify_setup.sh): checks CPU isolation, PMU counters and tools, then runs a pilot.
  - [`common/`](meta_profiling_deliverables/common/): `perf_stat_ruler.py` (outer ruler), `analyze_hypotheses.py` (statistics, Holm-Bonferroni), `generate_figures.py` (paper figures), system hardening scripts, and bpftrace scripts kept for diagnostics.
  - [`dummy_workload_test/`](meta_profiling_deliverables/dummy_workload_test/): H1/H2/H3 on a CPU-bound workload (`lat_mem_rd`).
  - [`go_api_test/`](meta_profiling_deliverables/go_api_test/): H1/H4 on the Go microservice, constant-work methodology.
  - [`results/`](meta_profiling_deliverables/results/): experiment outputs. `archive_bpftrace/` holds earlier data collected with the bpftrace ruler (see [docs/analysis_results.md](docs/analysis_results.md) for its known issues).

- **[`docs/`](docs/)**: hypothesis framework, NUMA topology analysis, code review findings and project notes.

- **[`microservice/`](microservice/)**: the target Go REST API, instrumented with Prometheus metrics. Exposes `/metrics`, `/healthz`, `/users` (I/O-bound) and `/compute` (CPU-bound).

- **[`config/`](config/)**: configuration for the surrounding observability stack (Prometheus, Nginx, PostgreSQL, Grafana).

- **[`loadtest/`](loadtest/)**: `k6` scripts for generating traffic against the microservice.

- **`docker-compose.yml`**: boots the multi-tier stack that the Go API experiments profile.

## CPU Roles

| CPU | Role |
|-----|------|
| 6  | workload (`lat_mem_rd`) |
| 7  | `perf record` (tool under study) |
| 14 | `perf stat` (outer ruler, other socket) |

Required kernel parameter: `isolcpus=6,7,14,15,22,23,30,31` (includes the hyper-thread siblings).

## Running the Pipeline

```bash
cd meta_profiling_deliverables

# 1. Verify the machine (isolation, PMU counters, tools) and run a short pilot
sudo bash verify_setup.sh --quick

# 2. Full run: H1–H4, analysis report and figures
sudo bash run_full_experiments.sh            # 500 iterations
sudo bash run_full_experiments.sh --pilot    # 50 iterations
```

Each run writes a session folder under `results/` containing the CSVs, the system state, `analysis_report.md` and `figures/`.
