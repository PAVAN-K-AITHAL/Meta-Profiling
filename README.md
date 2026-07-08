# Performance Analysis: Meta-Profiling

This repository contains the architecture, target application, and scripts for our **Meta-Profiling (Instrumentation of Instrumentation)** research project. 

Our research focuses on quantifying the "observer effect" of Linux profiling tools (like `perf`, `eBPF`, and `/proc`) on a multi-tier microservice architecture.

## Repository Structure

- **[`meta_profiling_deliverables/`](meta_profiling_deliverables/)**: The core deliverables for the meta-profiling thesis, including automated benchmark makefiles, Python statistical scripts (using Welch's t-test), eBPF probes, and core isolation setup. 
  - **Start here:** Please see the [Go API Experiment Guide](meta_profiling_deliverables/goapi_experiment_guide.md) for detailed instructions on running the automated meta-profiling pipeline.
  - Also review the [Meta-Profiling README](meta_profiling_deliverables/README.md) for an overview of the specific mentor requirements addressed (CPU isolation, statistical significance, eBPF counting, etc.).

- **[`microservice/`](microservice/)**: The target Go REST API. It is instrumented with custom Prometheus metrics and exposes `/metrics`, `/healthz`, `/users` (I/O-bound), and `/compute` (CPU-bound) endpoints.

- **[`config/`](config/)**: Configuration files for the surrounding observability stack (Prometheus, Nginx, PostgreSQL, Grafana).

- **[`loadtest/`](loadtest/)**: `k6` scripts used to generate stable, baseline traffic against the microservice during profiling.

- **`docker-compose.yml`**: The orchestration file that boots the entire multi-tier stack, providing a stable target for our profilers.

## Running the Meta-Profiling Pipeline

Everything is automated via Makefiles. To run a full 1000-iteration statistical test against the Go API:

```bash
cd meta_profiling_deliverables
make -f Makefile.goapi
```

This will automatically:
1. Boot the Docker stack.
2. Spin up `k6` to apply baseline load.
3. Discover the Go API PID on the host.
4. Execute `measure_statistically.py` (which internally orchestrates `perf record` on isolated CPUs and measures its overhead using `perf stat`).
5. Shut down the stack and output a statistically sound CSV of the overhead.
