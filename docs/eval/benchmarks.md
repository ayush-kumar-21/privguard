# DP overhead benchmarks (Phase 5.1)

`cargo bench -p privguard-plan --bench dp_overhead` (criterion, release profile, Apple silicon laptop,
20,000 visits over 4,000 patients, ~5 visits each, real heap files). Median of 20 samples.

Each query runs as the non-private plan (`exact`: Scan, Filter, Aggregate) and as the private plan
(`dp`: Scan, Filter, ContributionBound, Clamp, [PartitionSelect], DPAggregate).

| query | end-to-end exact | end-to-end DP | overhead | operators only, exact | operators only, DP | ratio |
|---|---:|---:|---:|---:|---:|---:|
| `COUNT(*)` | 5.68 ms | 5.95 ms | **+5%** | 1.34 ms | 3.10 ms | 2.3x |
| `SUM(total_charge) WHERE dept_id = 2` | 4.24 ms | 4.56 ms | **+8%** | 1.32 ms | 1.61 ms | 1.2x |
| `COUNT(*) GROUP BY admission_type` | 4.71 ms | 8.60 ms | **+83%** | 1.81 ms | 5.77 ms | 3.2x |

*End-to-end* includes building the plan; *operators only* builds it outside the timer.

**Reading the numbers**

- Scalar queries cost 5-8% end to end. The privacy operators are cheap next to reading the heap.
- Grouped queries pay for contribution bounding across groups and partition selection (a keyed
  noisy threshold per group): roughly 1.8x end to end.
- Nothing here is I/O-bound in the usual sense. `HeapScan` reads the whole heap into memory when the
  plan is built (`privguard-plan/src/exec.rs`), so the end-to-end column is dominated by that read.
  That is a scalability ceiling (memory grows with the table), not a DP cost; a streaming scan
  through the buffer pool is the fix if tables outgrow RAM.
- A first version of this benchmark timed the exact plan's build but not the DP plan's, which made
  DP look faster than the baseline. Both are now measured the same way, in both columns.
