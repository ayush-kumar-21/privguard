"""
Constrained SQL front-end for the DP gateway.

Analyst SQL is parsed with sqlglot into a validated `QuerySpec`; it is NEVER
executed as raw SQL against PostgreSQL. The spec is later realised as a
parameterised Django ORM query. This is the SQL-injection defence: the
analyst's text is *data to the parser*, not SQL to the database.

Supported subset (star-schema aggregate over the privacy-unit fact table):

    SELECT <agg>(<col|*>) FROM visits
      [WHERE <col> <op> <literal> [AND ...]]
      [GROUP BY <col>]

    agg   : COUNT | SUM | AVG
    op    : = | != | <> | > | >= | < | <=
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# Only the privacy-unit fact table is queryable in this slice.
FACT_TABLE = "visits"
PRIVACY_UNIT = "patient_id"

FILTERABLE = {"total_charge", "admission_type", "dept_id", "admit_ts", "discharge_ts"}
NUMERIC_COLS = {"total_charge"}
GROUPABLE = {"admission_type", "dept_id"}

_OP = {
    exp.EQ: "exact",
    exp.GT: "gt",
    exp.GTE: "gte",
    exp.LT: "lt",
    exp.LTE: "lte",
}


class DPQueryError(ValueError):
    """Raised for anything outside the supported subset."""


@dataclass
class QuerySpec:
    agg: str                       # COUNT | SUM | AVG
    column: str | None             # target column (None for COUNT(*))
    table: str
    filters: list[tuple[str, str, object]] = field(default_factory=list)  # (col, op, value); op 'ne' = not-equal
    group_by: str | None = None

    def canonical(self) -> str:
        parts = [self.agg, self.column or "*", self.table]
        for col, op, val in sorted(self.filters, key=lambda f: (f[0], f[1], str(f[2]))):
            parts.append(f"{col}{op}{val!r}")
        parts.append(f"GROUP={self.group_by or ''}")
        return "|".join(parts)

    def ast_hash(self) -> bytes:
        return hashlib.sha256(self.canonical().encode("utf-8")).digest()


def _literal_value(node: exp.Expression):
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        text = node.this
        try:
            return int(text)
        except ValueError:
            return float(text)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    raise DPQueryError(f"unsupported literal: {node.sql()}")


def _flatten_and(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _flatten_and(node.left) + _flatten_and(node.right)
    if isinstance(node, exp.Paren):
        return _flatten_and(node.this)
    return [node]


def _parse_predicate(node: exp.Expression) -> tuple[str, str, object]:
    if isinstance(node, (exp.NEQ,)):
        col, val = node.left, node.right
        op = "ne"
    elif type(node) in _OP:
        col, val = node.left, node.right
        op = _OP[type(node)]
    else:
        raise DPQueryError(f"unsupported WHERE clause: {node.sql()}")
    if not isinstance(col, exp.Column):
        raise DPQueryError(f"left of comparison must be a column: {node.sql()}")
    name = col.name
    if name not in FILTERABLE:
        raise DPQueryError(f"column not filterable: {name}")
    return name, op, _literal_value(val)


def parse(sql: str) -> QuerySpec:
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:  # noqa: BLE001
        raise DPQueryError(f"could not parse SQL: {exc}") from exc

    if not isinstance(tree, exp.Select):
        raise DPQueryError("only SELECT is supported")

    table_node = tree.find(exp.Table)
    if table_node is None or table_node.name != FACT_TABLE:
        raise DPQueryError(f"only FROM {FACT_TABLE} is supported")

    # aggregate
    agg_node = None
    for e in tree.expressions:
        found = e.find(exp.AggFunc)
        if found is not None:
            agg_node = found
            break
    if agg_node is None:
        raise DPQueryError("query must contain one aggregate (COUNT/SUM/AVG)")

    agg = type(agg_node).__name__.upper()
    if agg not in {"COUNT", "SUM", "AVG"}:
        raise DPQueryError(f"unsupported aggregate: {agg}")

    target = agg_node.this
    if isinstance(target, exp.Star) or target is None:
        column = None
    elif isinstance(target, exp.Column):
        column = target.name
    else:
        raise DPQueryError(f"unsupported aggregate argument: {agg_node.sql()}")

    if agg in {"SUM", "AVG"}:
        if column not in NUMERIC_COLS:
            raise DPQueryError(f"{agg} requires a numeric column {sorted(NUMERIC_COLS)}")
    if agg == "COUNT" and column not in (None,):
        raise DPQueryError("only COUNT(*) is supported")

    # where
    filters: list[tuple[str, str, object]] = []
    where = tree.args.get("where")
    if where is not None:
        for cmp_node in _flatten_and(where.this):
            filters.append(_parse_predicate(cmp_node))

    # group by
    group_by = None
    group = tree.args.get("group")
    if group is not None:
        cols = group.expressions
        if len(cols) != 1 or not isinstance(cols[0], exp.Column):
            raise DPQueryError("only a single GROUP BY column is supported")
        group_by = cols[0].name
        if group_by not in GROUPABLE:
            raise DPQueryError(f"column not groupable: {group_by}")

    return QuerySpec(agg=agg, column=column, table=FACT_TABLE, filters=filters, group_by=group_by)
