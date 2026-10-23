"""
The reference differentially-private executor. `SPEC.md` §4.

This is the oracle proper: given a dataset, a query spec, a policy, a budget and
a seed, it produces the answer the Rust engine must also produce. It runs over
plain Python lists and optimises for being obviously right.

Pipeline, matching the physical plan the engine builds (`SPEC.md` §4):

    filter -> contribution-bound -> clamp -> aggregate -> noise [-> partition-select]

The ordering is not stylistic. Bounding and clamping *must* precede aggregation,
because the noise is calibrated against the sensitivity those two steps
establish; aggregating first and clamping after would publish an answer whose
true sensitivity is unbounded.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

from .accounting import (
    eps_from_rho,
    grouped_l2_sensitivity,
    laplace_scale_from_rho,
    sigma_from_rho,
)
from .mechanisms import (
    GRID,
    KeyedRandom,
    clamp,
    noisy_count,
    noisy_gaussian,
    noisy_laplace,
    partition_cells,
    partition_cutoff,
)
from .spec import PRIVACY_UNIT, ColumnPolicy, DPQueryError, QuerySpec, TablePolicy

__all__ = ["DPResult", "execute", "apply_filters", "contribution_bound", "true_aggregate"]

# `SPEC.md` §4.5 — see privguard-privacy::defaults for the mirrored values.
DELTA_PARTITION = 1e-2
PARTITION_BUDGET_FRACTION = 0.2

_ORDER_KEY = "visit_id"


class DPResult:
    """The outcome of one private query."""

    __slots__ = ("rows", "rho_charged", "eps_equivalent", "mechanism",
                 "suppressed_groups", "sensitivity", "scale")

    def __init__(
        self,
        rows: list[dict],
        rho_charged: float,
        eps_equivalent: float,
        mechanism: str,
        sensitivity: float,
        scale: float,
        suppressed_groups: int = 0,
    ):
        self.rows = rows
        self.rho_charged = rho_charged
        self.eps_equivalent = eps_equivalent
        self.mechanism = mechanism
        self.sensitivity = sensitivity
        self.scale = scale
        self.suppressed_groups = suppressed_groups

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"DPResult(rows={self.rows!r}, rho={self.rho_charged}, "
            f"eps={self.eps_equivalent:.4f}, mechanism={self.mechanism!r}, "
            f"suppressed={self.suppressed_groups})"
        )


# ---------------------------------------------------------------- filtering
def _matches(row: dict, col: str, op: str, val: Any) -> bool:
    actual = row.get(col)
    if actual is None:
        # NULL compares false under every operator, including `ne` — SQL
        # three-valued logic, not Python's None ordering (which would raise).
        return False
    if op == "exact":
        return actual == val
    if op == "ne":
        return actual != val
    if op == "gt":
        return actual > val
    if op == "gte":
        return actual >= val
    if op == "lt":
        return actual < val
    if op == "lte":
        return actual <= val
    raise DPQueryError(f"unsupported operator: {op}")


def apply_filters(rows: Iterable[dict], filters: Sequence[tuple[str, str, Any]]) -> list[dict]:
    """Conjunctive WHERE. Only AND is supported (`SPEC.md` §3)."""
    return [r for r in rows if all(_matches(r, c, o, v) for c, o, v in filters)]


# ------------------------------------------------------- contribution bounds
def contribution_bound(
    rows: Sequence[dict], policy: TablePolicy, group_by: str | None
) -> list[dict]:
    """Apply the L0 and L-inf contribution bounds. `SPEC.md` §4.2.

    For a grouped query the L0 bound comes first: a privacy unit keeps its G
    lowest-sorting groups, and then at most C rows *within each* of those
    groups. So a unit contributes at most C*G rows overall, which is exactly
    what the sqrt(G)*C*M sensitivity accounts for.

    Selection is deterministic — groups by ascending value, rows by ascending
    primary key. Determinism is required, not merely tidy: the noise cache
    promises the same answer for the same fingerprint, and a bound that picked
    rows arbitrarily would break that promise on the first re-run.
    """
    by_unit: dict[Any, list[dict]] = defaultdict(list)
    for r in rows:
        by_unit[r[PRIVACY_UNIT]].append(r)

    kept: list[dict] = []
    for unit_rows in by_unit.values():
        if group_by is None:
            unit_rows = sorted(unit_rows, key=lambda r: r[_ORDER_KEY])
            kept.extend(unit_rows[: policy.max_rows_per_unit])
            continue

        # L0: keep the G lowest-sorting distinct group values for this unit.
        groups = sorted({r[group_by] for r in unit_rows})[: policy.max_groups_per_unit]
        allowed = set(groups)

        # L-inf: within each surviving group, keep the first C rows by key.
        per_group: dict[Any, list[dict]] = defaultdict(list)
        for r in unit_rows:
            if r[group_by] in allowed:
                per_group[r[group_by]].append(r)
        for grp_rows in per_group.values():
            grp_rows.sort(key=lambda r: r[_ORDER_KEY])
            kept.extend(grp_rows[: policy.max_rows_per_unit])

    kept.sort(key=lambda r: r[_ORDER_KEY])
    return kept


# ---------------------------------------------------------------- aggregation
def true_aggregate(
    rows: Sequence[dict], spec: QuerySpec, col_policy: ColumnPolicy | None
) -> dict | list[dict]:
    """The *unnoised* aggregate. Never released — noise is added by the caller."""
    def clamped_sum(subset: Sequence[dict]) -> float:
        assert spec.column is not None and col_policy is not None
        return sum(
            clamp(float(r[spec.column] or 0.0), col_policy.lower_clamp, col_policy.upper_clamp)
            for r in subset
        )

    if spec.group_by is None:
        out = {"n": len(rows)}
        if spec.column:
            out["total"] = clamped_sum(rows)
        return out

    buckets: dict[Any, list[dict]] = defaultdict(list)
    for r in rows:
        buckets[r[spec.group_by]].append(r)

    grouped = []
    for key in sorted(buckets):
        subset = buckets[key]
        entry = {
            spec.group_by: key,
            "n": len(subset),
            "units": len({r[PRIVACY_UNIT] for r in subset}),
        }
        if spec.column:
            entry["total"] = clamped_sum(subset)
        grouped.append(entry)
    return grouped


# ------------------------------------------------------------------ execution
def _scalar(
    spec: QuerySpec,
    agg: dict,
    table_policy: TablePolicy,
    col_policy: ColumnPolicy | None,
    rho: float,
    rng: KeyedRandom,
) -> tuple[list[dict], float, float, str]:
    C = table_policy.max_rows_per_unit
    M = col_policy.magnitude if col_policy else 1.0

    if spec.agg == "COUNT":
        sensitivity = float(C)
        scale = laplace_scale_from_rho(sensitivity, rho)
        value = noisy_count(agg["n"], sensitivity, rho, rng)
        return [{"value": round(value, 4)}], sensitivity, scale, "discrete_laplace"

    if spec.agg == "SUM":
        sensitivity = C * M
        scale = laplace_scale_from_rho(sensitivity, rho)
        value = noisy_laplace(agg["total"], sensitivity, rho, rng)
        return [{"value": round(value, 4)}], sensitivity, scale, "laplace"

    # AVG = noisy_sum / max(noisy_count, 1), budget split 50/50 (`SPEC.md` §4.3).
    # The max(..., 1) keeps a noisy count that went negative from flipping the
    # sign of the reported average, which would be a startling public output.
    rho_half = rho / 2.0
    sum_sens, cnt_sens = C * M, float(C)
    sum_scale = laplace_scale_from_rho(sum_sens, rho_half)
    cnt_scale = laplace_scale_from_rho(cnt_sens, rho_half)
    noisy_sum = noisy_laplace(agg["total"], sum_sens, rho_half, rng)
    noisy_cnt = noisy_laplace(float(agg["n"]), cnt_sens, rho_half, rng)
    value = noisy_sum / max(noisy_cnt, 1.0)
    return [{"value": round(value, 4)}], sum_sens, sum_scale, "laplace"


def _grouped(
    spec: QuerySpec,
    groups: list[dict],
    table_policy: TablePolicy,
    col_policy: ColumnPolicy | None,
    rho: float,
    seed: bytes,
    delta_partition: float,
) -> tuple[list[dict], int, float, float]:
    C = table_policy.max_rows_per_unit
    G = table_policy.max_groups_per_unit
    M = col_policy.magnitude if col_policy else 1.0

    rho_part = rho * PARTITION_BUDGET_FRACTION
    rho_agg = rho - rho_part
    part_scale, cutoff = partition_cutoff(rho_part, delta_partition)

    # Two independent sub-streams so that the partition decision and the value
    # noise cannot be correlated by an analyst who learns one of them.
    part_rng = KeyedRandom(seed + b"partition")
    agg_rng = KeyedRandom(seed + b"aggregate")

    per_unit_sensitivity = M if spec.agg == "SUM" else 1.0
    delta2 = grouped_l2_sensitivity(G, C * per_unit_sensitivity)
    sigma = sigma_from_rho(delta2, rho_agg)

    released: list[dict] = []
    suppressed = 0
    for g in groups:
        if partition_cells(g["units"], part_scale, part_rng) < GRID + cutoff:
            suppressed += 1
            continue
        true_val = g["total"] if spec.agg == "SUM" else g["n"]
        released.append(
            {spec.group_by: g[spec.group_by], "value": round(noisy_gaussian(true_val, C * per_unit_sensitivity, G, rho_agg, agg_rng), 4)}
        )
    return released, suppressed, delta2, sigma


def execute(
    spec: QuerySpec,
    rows: Sequence[dict],
    *,
    rho: float,
    seed: bytes,
    table_policy: TablePolicy | None = None,
    column_policy: ColumnPolicy | None = None,
    delta: float = 1e-6,
    delta_partition: float = DELTA_PARTITION,
) -> DPResult:
    """Answer one query under differential privacy.

    `seed` comes from `mechanisms.derive_seed`. Passing the same spec, rows,
    rho and seed twice returns the identical answer — that reproducibility is
    what the noise cache relies on and what the engine is tested against.
    """
    if rho <= 0:
        raise DPQueryError("rho must be positive")

    table_policy = table_policy or TablePolicy()
    if spec.agg in ("SUM", "AVG") and column_policy is None:
        raise DPQueryError(
            f"{spec.agg}({spec.column}) needs a column policy with clamp bounds"
        )

    filtered = apply_filters(rows, spec.filters)
    bounded = contribution_bound(filtered, table_policy, spec.group_by)
    agg = true_aggregate(bounded, spec, column_policy)

    if spec.group_by is None:
        rng = KeyedRandom(seed)
        result, sensitivity, scale, mechanism = _scalar(
            spec, agg, table_policy, column_policy, rho, rng
        )
        suppressed = 0
    else:
        result, suppressed, sensitivity, scale = _grouped(
            spec, agg, table_policy, column_policy, rho, seed, delta_partition
        )
        mechanism = "gaussian"

    return DPResult(
        rows=result,
        rho_charged=rho,
        eps_equivalent=eps_from_rho(rho, delta),
        mechanism=mechanism,
        sensitivity=sensitivity,
        scale=scale,
        suppressed_groups=suppressed,
    )
