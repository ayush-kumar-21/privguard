"""
Query specification, canonical form and fingerprint. `SPEC.md` §3 and §4.6.

The canonical form is the load-bearing part of this module. Two queries that
mean the same thing must produce the same fingerprint, or the noise cache leaks:
an analyst who can spell one question two ways gets two independent noise draws
and can average them.

Canonical form (`SPEC.md` §4.6):

    agg | column-or-* | table | (col,op,value)* sorted | GROUP=<col-or-empty>

Sorting the predicates is what makes `a>1 AND b=2` and `b=2 AND a>1` one
fingerprint rather than two.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DPQueryError",
    "QuerySpec",
    "TablePolicy",
    "ColumnPolicy",
    "FACT_TABLE",
    "PRIVACY_UNIT",
    "FILTERABLE",
    "NUMERIC_COLS",
    "GROUPABLE",
    "AGGREGATES",
    "OPERATORS",
]

# The privacy-unit fact table and the unit itself. `SPEC.md` §2.1.
FACT_TABLE = "visits"
PRIVACY_UNIT = "patient_id"

FILTERABLE = frozenset(
    {"total_charge", "admission_type", "dept_id", "admit_ts", "discharge_ts"}
)
NUMERIC_COLS = frozenset({"total_charge"})
GROUPABLE = frozenset({"admission_type", "dept_id"})
AGGREGATES = frozenset({"COUNT", "SUM", "AVG"})

# Canonical operator spellings. These strings are hashed into the fingerprint,
# so they are part of the on-disk format: changing one invalidates every cached
# answer. Mirrored in engine/crates/privguard-sql/src/lib.rs.
OPERATORS = frozenset({"exact", "ne", "gt", "gte", "lt", "lte"})


class DPQueryError(ValueError):
    """Anything outside the supported subset, or forbidden by policy."""


@dataclass(frozen=True)
class TablePolicy:
    """Contribution bounds for a fact table. `SPEC.md` §4.2."""

    max_rows_per_unit: int = 5      # C, the L-inf bound
    max_groups_per_unit: int = 3    # G, the L0 bound

    def __post_init__(self) -> None:
        if self.max_rows_per_unit < 1:
            raise DPQueryError("max_rows_per_unit (C) must be at least 1")
        if self.max_groups_per_unit < 1:
            raise DPQueryError("max_groups_per_unit (G) must be at least 1")


@dataclass(frozen=True)
class ColumnPolicy:
    """Mechanism and clamp bounds for an aggregatable column. `SPEC.md` §4.3."""

    mechanism: str
    lower_clamp: float
    upper_clamp: float

    def __post_init__(self) -> None:
        if self.lower_clamp > self.upper_clamp:
            raise DPQueryError(
                f"clamp bounds inverted: [{self.lower_clamp}, {self.upper_clamp}]"
            )
        if self.magnitude == 0.0:
            # M = 0 makes SUM sensitivity zero, i.e. "release the true sum with
            # no noise". Refuse rather than silently publish.
            raise DPQueryError("clamp bounds [0, 0] give a zero-sensitivity policy")

    @property
    def magnitude(self) -> float:
        """M = max(|lo|, |hi|) — the per-row contribution bound."""
        return max(abs(self.lower_clamp), abs(self.upper_clamp))


@dataclass(frozen=True)
class QuerySpec:
    """A parsed, validated analyst query.

    `filters` is a list of `(column, canonical_op, value)`.
    """

    agg: str
    column: str | None
    table: str = FACT_TABLE
    filters: tuple[tuple[str, str, Any], ...] = field(default_factory=tuple)
    group_by: str | None = None

    def __post_init__(self) -> None:
        if self.agg not in AGGREGATES:
            raise DPQueryError(f"unsupported aggregate: {self.agg}")
        if self.table != FACT_TABLE:
            raise DPQueryError(f"only FROM {FACT_TABLE} is supported")

        if self.agg == "COUNT":
            if self.column is not None:
                raise DPQueryError("only COUNT(*) is supported")
        else:
            if self.column not in NUMERIC_COLS:
                raise DPQueryError(
                    f"{self.agg} requires a numeric column {sorted(NUMERIC_COLS)}"
                )

        if self.agg == "AVG" and self.group_by is not None:
            raise DPQueryError("AVG with GROUP BY is not supported in this version")

        for col, op, _ in self.filters:
            if col not in FILTERABLE:
                raise DPQueryError(f"column not filterable: {col}")
            if op not in OPERATORS:
                raise DPQueryError(f"unsupported operator: {op}")

        if self.group_by is not None and self.group_by not in GROUPABLE:
            raise DPQueryError(f"column not groupable: {self.group_by}")

    def canonical(self) -> str:
        """Canonical string form. `SPEC.md` §4.6.

        Predicates are sorted so that clause order does not change the
        fingerprint. `repr` on the value distinguishes 1 from "1", which
        matters: they select different rows.
        """
        parts = [self.agg, self.column or "*", self.table]
        for col, op, val in sorted(self.filters, key=lambda f: (f[0], f[1], str(f[2]))):
            parts.append(f"{col}{op}{val!r}")
        parts.append(f"GROUP={self.group_by or ''}")
        return "|".join(parts)

    def ast_hash(self) -> bytes:
        """SHA-256 of the canonical form — the query fingerprint."""
        return hashlib.sha256(self.canonical().encode("utf-8")).digest()

    def fingerprint_hex(self) -> str:
        return self.ast_hash().hex()
