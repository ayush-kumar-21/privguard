"""The seam between the gateway and the engine.

The gateway never touches data. It authenticates a principal and hands the SQL text to an
[`EngineClient`]. Phase 2 ships [`StubEngine`], which returns canned answers while still
behaving like the real thing where the UI can see it: budget is charged, repeats are cache
hits that cost nothing, unsupported SQL is refused with the `SPEC.md` §9 error classes, and the
audit log fills up. Phase 3 replaces it with a psycopg client speaking pgwire to the engine.
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .store import DEMO_PRINCIPALS, Principal

DELTA = 1e-6

#: Error classes of `SPEC.md` §9, the HTTP status each maps to, and the engine's SQLSTATE.
HTTP_STATUS = {"parse": 400, "policy": 403, "budget": 429, "catalog": 404, "engine": 500}
SQLSTATE = {
    "parse": "42601",
    "policy": "42501",
    "budget": "53400",
    "catalog": "42P01",
    "engine": "XX000",
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
    latency_ms: float
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


def _canonical(sql: str) -> str:
    """Whitespace- and case-insensitive form; the stub's stand-in for the query fingerprint."""
    return re.sub(r"\s+", " ", sql.strip().rstrip(";").strip()).lower()


def parse_stub(sql: str) -> tuple[str, str | None]:
    """Recognise the `SPEC.md` §3 subset. Returns `(aggregate, group_by)`."""
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
    return agg, group.lower() if group else None


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
        agg, group = parse_stub(sql)
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
