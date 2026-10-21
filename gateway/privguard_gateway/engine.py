"""The seam between the gateway and the engine.

The gateway never touches data. It authenticates a principal and hands the SQL text to an
[`EngineClient`]. Phase 2 ships [`StubEngine`], which returns canned answers while still
behaving like the real thing where the UI can see it: budget is charged, repeats are cache
hits that cost nothing, unsupported SQL is refused with the `SPEC.md` §9 error classes, and the
audit log fills up. Phase 3 replaces it with a psycopg client speaking pgwire to the engine.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .store import DEMO_PRINCIPALS, Principal

DELTA = 1e-6

#: Error classes of `SPEC.md` §9, the HTTP status each maps to, and the engine's SQLSTATE.
HTTP_STATUS = {
    "parse": 400,
    "policy": 403,
    "budget": 429,
    "catalog": 404,
    "engine": 500,
    "unavailable": 503,
}
SQLSTATE = {
    "parse": "42601",
    "policy": "42501",
    "budget": "53400",
    "catalog": "42P01",
    "engine": "XX000",
    "unavailable": "08006",
}


class EngineError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.kind]

    @property
    def sqlstate(self) -> str:
        return SQLSTATE[self.kind]


@dataclass(frozen=True)
class QueryOutcome:
    status: str  # executed | cache_hit
    result: list[dict]
    rho_charged: float
    epsilon_charged: float
    mechanism: str
    query_id: int
    suppressed_groups: int
    budget: dict


@dataclass(frozen=True)
class AuditRow:
    query_id: int
    principal_name: str
    status: str
    rho_charged: float
    delta_charged: float
    latency_ms: float | None
    sql_text: str
    submitted_at: str


@dataclass(frozen=True)
class BudgetView:
    account_id: int
    principal_name: str
    rho_total: float
    rho_spent: float
    rho_reserved: float
    rho_remaining: float
    delta_target: float | None
    window_type: str
    status: str


class EngineClient(Protocol):
    def query(self, principal: Principal, sql: str, rho: float) -> QueryOutcome: ...

    def budget(self, principal: Principal) -> BudgetView | None: ...

    def budgets(self, principal: Principal) -> list[BudgetView]: ...

    def verify_ledger(self, principal: Principal) -> dict | None: ...

    def audit(self, principal: Principal) -> list[AuditRow]: ...

    def explain(self, principal: Principal, sql: str) -> dict: ...

    def policy_grid(self, principal: Principal) -> dict: ...

    def set_grant(
        self, principal: Principal, role: str, table: str, column: str, granted: bool
    ) -> None: ...


def epsilon_from_rho(rho: float, delta: float = DELTA) -> float:
    """The (epsilon, delta) reporting projection of a rho spend (`SPEC.md` §4.1)."""
    return rho + 2.0 * math.sqrt(rho * math.log(1.0 / delta))


# ------------------------------------------------------------------- stub SQL
_PRED = (
    r"(?:total_charge|admission_type|dept_id|admit_ts|discharge_ts)"
    r"\s*(?:=|!=|<>|>=|<=|>|<)\s*(?:'[^']*'|-?\d+(?:\.\d+)?)"
)
_QUERY = re.compile(
    r"^\s*select\s+(?P<agg>count\(\s*\*\s*\)|sum\(\s*total_charge\s*\)|avg\(\s*total_charge\s*\))"
    r"\s+from\s+visits"
    rf"(?:\s+where\s+{_PRED}(?:\s+and\s+{_PRED})*)?"
    r"(?:\s+group\s+by\s+(?P<group>admission_type|dept_id))?"
    r"\s*;?\s*$",
    re.IGNORECASE,
)

# Canned answers. A real engine computes these; the stub only has to look plausible.
_SCALAR = {"count": 196, "sum": 98123.45, "avg": 512.3}
_GROUPED = {
    ("count", "admission_type"): [("ELECTIVE", 82), ("EMERGENCY", 71), ("URGENT", 40)],
    ("count", "dept_id"): [(1, 66), (2, 71), (3, 59)],
    ("sum", "admission_type"): [("ELECTIVE", 41234.5), ("EMERGENCY", 38002.1), ("URGENT", 18886.9)],
    ("sum", "dept_id"): [(1, 33120.4), (2, 36408.0), (3, 28595.0)],
}


# Fact-table columns as the engine reports them: (column, type, access, filterable, groupable, clamp).
_COLUMNS = [
    ("visit_id", "i64", "aggregate_only", False, False, None),
    ("patient_id", "i64", "denied", False, False, None),
    ("dept_id", "i64", "allowed", True, True, None),
    ("attending_staff_id", "i64", "aggregate_only", False, False, None),
    ("admit_ts", "timestamp", "allowed", True, False, None),
    ("discharge_ts", "timestamp", "allowed", True, False, None),
    ("total_charge", "f64", "aggregate_only", True, False, (0.0, 1000.0)),
    ("admission_type", "text", "allowed", True, True, None),
]
_GRANTABLE = ("dept_id", "total_charge", "admission_type", "admit_ts", "discharge_ts")
_ROLES = ("auditor", "clinician", "dba", "dp_analyst")


def _canonical(sql: str) -> str:
    """Whitespace- and case-insensitive form; the stub's stand-in for the query fingerprint."""
    return re.sub(r"\s+", " ", sql.strip().rstrip(";").strip()).lower()


def parse_stub(sql: str) -> tuple[str, str | None, bool]:
    """Recognise the `SPEC.md` §3 subset. Returns `(aggregate, group_by, has_where)`."""
    m = _QUERY.match(sql)
    if not m:
        raise EngineError(
            "parse",
            "unsupported SQL: expected SELECT COUNT(*)|SUM(total_charge)|AVG(total_charge) "
            "FROM visits [WHERE <col> <op> <literal> [AND ...]] [GROUP BY admission_type|dept_id]",
        )
    agg = m.group("agg").split("(")[0].lower()
    group = m.group("group")
    if agg == "avg" and group:
        raise EngineError("parse", "AVG with GROUP BY is not supported")
    has_where = re.search(r"\swhere\s", sql, re.IGNORECASE) is not None
    return agg, group.lower() if group else None, has_where


class StubEngine:
    """Canned answers with real budget, cache and audit behaviour."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_query = 1
        # username -> [rho_total, rho_spent]
        self._accounts = {
            name: [total, 0.0] for name, (_, total) in DEMO_PRINCIPALS.items() if total is not None
        }
        self._account_ids = {name: i for i, name in enumerate(self._accounts, start=1)}
        self._cache: dict[tuple[str, str], QueryOutcome] = {}
        self._audit: list[AuditRow] = []
        self._ledger_entries: dict[str, int] = {}
        self._grants = {
            (role, "visits", col)
            for role in ("dp_analyst", "auditor")
            for col in _GRANTABLE
        }

    # -- helpers (callers hold the lock)
    def _view(self, name: str) -> BudgetView:
        total, spent = self._accounts[name]
        remaining = max(total - spent, 0.0)
        return BudgetView(
            account_id=self._account_ids[name],
            principal_name=name,
            rho_total=total,
            rho_spent=spent,
            rho_reserved=0.0,
            rho_remaining=remaining,
            delta_target=DELTA,
            window_type="lifetime",
            status="active" if remaining > 0 else "exhausted",
        )

    def _log(self, name: str, status: str, rho: float, sql: str) -> int:
        qid = self._next_query
        self._next_query += 1
        self._audit.append(
            AuditRow(
                query_id=qid,
                principal_name=name,
                status=status,
                rho_charged=rho,
                delta_charged=0.0,
                latency_ms=1.0,
                sql_text=sql,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        return qid

    # -- EngineClient
    def query(self, principal: Principal, sql: str, rho: float) -> QueryOutcome:
        agg, group, _ = parse_stub(sql)
        name = principal.username
        with self._lock:
            if name not in self._accounts:
                self._log(name, "rejected_policy", 0.0, sql)
                raise EngineError("policy", "this principal has no budget account")

            key = (name, _canonical(sql))
            cached = self._cache.get(key)
            if cached is not None:
                qid = self._log(name, "cache_hit", 0.0, sql)
                return QueryOutcome(
                    status="cache_hit",
                    result=cached.result,
                    rho_charged=0.0,
                    epsilon_charged=0.0,
                    mechanism=cached.mechanism,
                    query_id=qid,
                    suppressed_groups=cached.suppressed_groups,
                    budget=self._budget_dict(name),
                )

            total, spent = self._accounts[name]
            if spent + rho > total + 1e-12:
                self._log(name, "rejected_budget", 0.0, sql)
                raise EngineError(
                    "budget",
                    f"budget exhausted: {max(total - spent, 0.0):.4f} rho left, {rho} requested",
                )
            self._accounts[name][1] = spent + rho
            self._ledger_entries[name] = self._ledger_entries.get(name, 0) + 2  # reserve + commit

            if group is None:
                value = _SCALAR[agg]
                result = [{"value": value}]
                mechanism = "discrete_laplace" if agg == "count" else "laplace"
                suppressed = 0
            else:
                result = [{group: k, "value": v} for k, v in _GROUPED[(agg, group)]]
                mechanism = "gaussian"
                suppressed = 1

            qid = self._log(name, "executed", rho, sql)
            outcome = QueryOutcome(
                status="executed",
                result=result,
                rho_charged=rho,
                epsilon_charged=epsilon_from_rho(rho),
                mechanism=mechanism,
                query_id=qid,
                suppressed_groups=suppressed,
                budget=self._budget_dict(name),
            )
            self._cache[key] = outcome
            return outcome

    def _budget_dict(self, name: str) -> dict:
        v = self._view(name)
        return {
            "rho_total": v.rho_total,
            "rho_spent": v.rho_spent,
            "rho_reserved": v.rho_reserved,
            "rho_remaining": v.rho_remaining,
            "status": v.status,
        }

    def budget(self, principal: Principal) -> BudgetView | None:
        with self._lock:
            return self._view(principal.username) if principal.username in self._accounts else None

    def budgets(self, principal: Principal) -> list[BudgetView]:
        with self._lock:
            names = list(self._accounts) if principal.is_privileged else [principal.username]
            return [self._view(n) for n in names if n in self._accounts]

    def verify_ledger(self, principal: Principal) -> dict | None:
        with self._lock:
            if principal.username not in self._accounts:
                return None
            return {
                "ok": True,
                "entries": self._ledger_entries.get(principal.username, 0),
                "first_bad_entry": None,
            }

    def audit(self, principal: Principal) -> list[AuditRow]:
        with self._lock:
            rows = list(self._audit)
        if principal.is_privileged:
            return rows
        return [r for r in rows if r.principal_name == principal.username]

    def explain(self, principal: Principal, sql: str) -> dict:
        agg, group, has_where = parse_stub(sql)
        physical = ["Scan"] + (["Filter"] if has_where else []) + ["ContributionBound"]
        if agg != "count":
            physical.append("Clamp")
        if group:
            physical.append("PartitionSelect")
        physical.append("DPAggregate")
        logical = ["Aggregate"] + (["Filter"] if has_where else []) + ["Scan"]
        with self._lock:
            cached = (principal.username, _canonical(sql)) in self._cache
        return {
            "fingerprint": hashlib.sha256(_canonical(sql).encode()).hexdigest(),
            "cached": cached,
            "logical": logical,
            "logical_text": "\n".join("  " * i + op for i, op in enumerate(logical)) + "\n",
            "physical": physical,
            "mechanism": "gaussian" if group else ("discrete_laplace" if agg == "count" else "laplace"),
            "sensitivity": 5.0,
            "privacy_unit": "patient_id",
        }

    # -- policy grid
    def policy_grid(self, principal: Principal) -> dict:
        if not principal.is_privileged:
            raise EngineError("policy", "requires one of the roles: dba, auditor")
        with self._lock:
            grants = set(self._grants)
        return {
            "roles": list(_ROLES),
            "columns": [
                {
                    "table": "visits",
                    "column": col,
                    "type": ty,
                    "access": access,
                    "filterable": filt,
                    "groupable": grp,
                    "mechanism": "laplace" if clamp else None,
                    "lower_clamp": clamp[0] if clamp else None,
                    "upper_clamp": clamp[1] if clamp else None,
                    "grants": {r: (r, "visits", col) in grants for r in _ROLES},
                }
                for col, ty, access, filt, grp, clamp in _COLUMNS
            ],
        }

    def set_grant(
        self, principal: Principal, role: str, table: str, column: str, granted: bool
    ) -> None:
        if "dba" not in principal.roles:
            raise EngineError("policy", "requires one of the roles: dba")
        if role not in _ROLES:
            raise EngineError("catalog", f"unknown role: {role}")
        if table != "visits" or column not in {c[0] for c in _COLUMNS}:
            raise EngineError("catalog", f"unknown column: {table}.{column}")
        with self._lock:
            (self._grants.add if granted else self._grants.discard)((role, table, column))
