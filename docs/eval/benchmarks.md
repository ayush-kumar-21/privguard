# DP overhead benchmarks (Phase 5.1)

`cargo bench -p privguard-plan --bench dp_overhead` (criterion, release profile, Apple silicon laptop,
20,000 visits over 4,000 patients, ~5 visits each, real heap files). Median of 20 samples.

Each query runs as the non-private plan (`exact`: Scan, Filter, Aggregate) and as the private plan
(`dp`: Scan, Filter, ContributionBound, Clamp, [PartitionSelect], DPAggregate).

| query | end-to-end exact | end-to-end DP | overhead | operators only, exact | operators only, DP | ratio |
|---|---:|---:|---:|---:|---:|---:|
| `COUNT(*)` | 4.12 ms | 6.22 ms | **+51%** | 1.33 ms | 3.29 ms | 2.5x |
| `SUM(total_charge) WHERE dept_id = 2` | 4.15 ms | 4.47 ms | **+8%** | 1.34 ms | 1.62 ms | 1.2x |
| `COUNT(*) GROUP BY admission_type` | 4.63 ms | 8.47 ms | **+83%** | 1.85 ms | 5.69 ms | 3.1x |

Measured after the exact integer samplers (`SPEC.md` A5) replaced the float ones; the DP column did not
move, so the samplers cost nothing measurable next to the scan. Run-to-run noise on a laptop is large: the
exact `COUNT` baseline read 5.7 ms in an earlier run and 4.1 ms in this one, which is why the `COUNT` overhead
swung from +5% to +51%. Treat scalar overheads as "between a few percent and about 50%", not as a point
estimate; the grouped figure is stable at about +80%.

*End-to-end* includes building the plan; *operators only* builds it outside the timer.

**Reading the numbers**

- Scalar queries cost roughly 8-50% end to end (see the noise note above). The privacy operators are cheap next to reading the heap.
- Grouped queries pay for contribution bounding across groups and partition selection (a keyed
  noisy threshold per group): roughly 1.8x end to end.
- Nothing here is I/O-bound in the usual sense. `HeapScan` reads the whole heap into memory when the
  plan is built (`privguard-plan/src/exec.rs`), so the end-to-end column is dominated by that read.
  That is a scalability ceiling (memory grows with the table), not a DP cost; a streaming scan
  through the buffer pool is the fix if tables outgrow RAM.
- A first version of this benchmark timed the exact plan's build but not the DP plan's, which made
  DP look faster than the baseline. Both are now measured the same way, in both columns.
