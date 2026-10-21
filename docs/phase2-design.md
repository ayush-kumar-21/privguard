# Phase 2 design note

Covers the pieces of Phases 2 and 3 that have no frozen layout in `SPEC.md`: B+ tree
nodes, tuple encoding, catalog tables, ledger entries and the operator interfaces. Where the
SPEC already decides something this note only points at it.

## 1. Tuple encoding (`privguard-storage::tuple`)

`SPEC.md` §2.3. A tuple is `null bitmap ‖ values`:

- bitmap: `ceil(ncols / 8)` bytes, bit `i` set means column `i` is NULL;
- values follow in column order, NULL columns are skipped;
- `I32`/`I64`/`F64`/`Timestamp` little-endian fixed width, `Bool` one byte,
  `Text` a `u16` length prefix then UTF-8 (so a text value is at most 65 535 bytes).

Decoding needs the column types, so the schema always travels separately (catalog or the
hard-coded `sys_*` schemas).

## 2. B+ tree (`privguard-storage::btree`)

`SPEC.md` §5.4. One tree per index file, over any `PageStore`.

- Key: `i64`. Payload: a packed `Rid` (`Rid::to_u64`). Non-unique indexes (`visits.patient_id`)
  are supported by ordering entries on the pair `(key, rid)`, so every entry is distinct and a
  point lookup returns every RID with that key.
- Page 0 is a `FileHeader` page whose slot 0 holds `root: u32 | height: u32 | entries: u64`.
- Every node is one `SlottedPage` (so it carries the CRC and `page_kind`) holding a single
  tuple, the encoded node. That keeps the node format independent of slot bookkeeping.

```
leaf      (BTreeLeaf)    count u16 | next u32 (0 = none) | count × (key i64, rid u64)      16 B/entry
internal  (BTreeInternal) count u16 | child0 u32 | count × (key i64, rid u64, child u32)   20 B/entry
```

Capacity: 509 leaf entries, 407 separators per internal node. The constructor also takes
smaller caps so the tests can force splits and merges with a handful of keys.

- Descent: in an internal node take child `i` = number of separators `<= (key, rid)`.
  Separator `(k, r)` means the right subtree holds entries `>= (k, r)`.
- Insert: insert into the leaf; on overflow split in half, the right node's first entry is
  copied up as the separator (leaf) or the middle separator moves up (internal). A root split
  allocates a new root.
- Delete: remove from the leaf; below half occupancy borrow from a sibling under the same
  parent, otherwise merge with it and delete the separator from the parent, recursively. A root
  with a single child is replaced by that child.
- Range scan: descend to the lower bound, then follow the right-sibling pointers.
- Freed pages are not reclaimed. No free list in v1; merges leak the page. Acceptable because
  the workload is load-then-read.

## 3. Catalog (`privguard-catalog`)

`SPEC.md` §7. Each `sys_*` table is a heap file `<dir>/<name>.heap` (`FilePageStore`). Their
schemas are hard-coded in `catalog::schema`; everything else is read from those files.

| Table | Columns |
|---|---|
| `sys_database` | `name Text`, `default_delta F64` |
| `sys_table` | `table_id I64`, `name Text`, `is_fact Bool`, `row_estimate I64` |
| `sys_column` | `table_id I64`, `ordinal I32`, `name Text`, `col_type Text`, `nullable Bool`, `encrypted Bool`, `filterable Bool`, `groupable Bool` |
| `sys_table_privacy` | `table_id I64`, `max_rows_per_unit I32`, `max_groups_per_unit I32` |
| `sys_column_privacy` | `table_id I64`, `column Text`, `mechanism Text`, `lower_clamp F64`, `upper_clamp F64`, `access Text` |

The RBAC, budget, query and noise-cache tables are created empty with their schemas so the
bootstrap list in `BOOTSTRAP_TABLES` is real; Phase 3 fills them.

The `Catalog` keeps an in-memory copy and `save()` rewrites the heap files. The catalog is
tiny and written rarely (load time), so rewrite is simpler than in-place update.

## 4. Loader (`privguard-catalog::loader`)

Reads the CSVs written by `oracle/privguard_oracle/dataset.py` (`data/safehealth/*.csv`), creates
the `SPEC.md` §2.2 tables in the catalog, inserts tuples into `<dir>/<table>.heap` and builds
B+ tree indexes on the primary key and on `visits.patient_id`.

## 5. Logical plan (`privguard-plan::logical`)

`BoundQuery` -> `Scan(table)` -> `Filter(preds)` -> `Aggregate(agg, column, group_by)`. The
physical DP pipeline is chosen in Phase 3.

## 6. DP operators (`privguard-privacy::operators`)

All operators are `RowSource` -> `RowSource` and blocking where they must be. Configuration
carries column positions, so they are independent of the table schema.

```
Scan -> Filter -> ContributionBound -> Clamp -> [PartitionSelect] -> DPAggregate
```

`PartitionSelect` moves in front of `DPAggregate` (the Phase 0 placeholder had it last). The
oracle (and `SPEC.md` §4.5, "survivors are then noised") draws the aggregate noise only for
groups that survive, from a separate keyed stream. Reproducing the oracle bit-for-bit
therefore needs the survivors known before noising. See `SPEC.md` §10, A1.

- `ContributionBound(unit, order_key, group?, C, G)`: per unit keep the `G` smallest group
  values, then the first `C` rows per group by order key; output sorted by order key.
- `Clamp(value_col, lo, hi)`: NULL counts as 0, then clamp. Output column is `F64`.
- `PartitionSelect(unit, group, rho_part, delta_part, seed)`: distinct units per group plus
  `Laplace(b_part)` against `tau`; emits only rows of surviving groups, reports the suppressed
  count through a shared counter.
- `DPAggregate(agg, value?, group?, policies, rho, seed)`: exact aggregate then noise. Scalar
  `COUNT` uses discrete Laplace, `SUM`/`AVG` Laplace; grouped uses Gaussian at `0.8·rho`.
  Output: scalar `[value]`, grouped `[group, value]` in ascending group order.

## 7. Budget ledger (`privguard-privacy::ledger`)

`SPEC.md` §6, one chain per account, `hash = blake3(seq ‖ account_id ‖ query_id ‖ kind ‖
rho_bits ‖ prev_hash)`, integers big-endian. Entries convert to and from `sys_budget_ledger`
rows (`LedgerEntry::to_row` / `from_row`); `BudgetLedger::restore` rebuilds balances by
replaying the entries and re-verifies the whole chain, so loading tampered rows fails. The
ledger itself is storage-agnostic; writing the rows to the heap file is Phase 3 glue. Only the
fields in the hash formula are
covered by the chain; `mechanism`, `sensitivity`, `scale` and `eps_equiv` are advisory
metadata. Dangling reservations are aborted by `abort_dangling()` at startup instead of a timed
reaper (there is no clock in the ledger).

## 8. Gateway scaffold (`gateway/`)

FastAPI + python-jose (HS256 JWT) + argon2-cffi. Endpoints mirror what the frontend already
calls on the Django API. The engine is behind an `EngineClient` protocol; Phase 2 ships a
`StubEngine` with canned results, Phase 3 swaps in the psycopg client.
