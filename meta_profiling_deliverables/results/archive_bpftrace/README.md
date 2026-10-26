# Archived results (bpftrace ruler)

Data in this folder was collected **before** the switch to `perf stat` as the
outer ruler (July 2026). It is kept for provenance and "before vs after"
comparison only. Do not use it for the paper's claims.

Known problems with this data (details in [docs/analysis_results.md](../../../docs/analysis_results.md)):

- bpftrace counts in multiples of its overflow period (e.g. 10K cycles), so
  values are quantized.
- bpftrace NMIs fired on the CPUs being measured (observer effect), and PMU
  counters were shared with `perf record`.
- Go API H4 data was never analyzed.

| File | Contents |
|------|----------|
| `hypotheses_dummy_20260715_161841.csv` | Dummy workload H1/H2/H3 pilot (bpftrace ruler) |
| `hypotheses_dummy_20260714_163230.csv` | Earlier dummy workload run |
| `perf_record_overhead_pilot_dummy.md` | Write-up of the 20260715 pilot |
| `ebpf_overhead_20260702_163224.csv` | Single-shot dummy workload measurement |
| `goapi_overhead_20260702_180722.csv`, `goapi_ebpf_overhead_20260707_122926.csv` | Single-shot Go API measurements |
