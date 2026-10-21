"""The real engine, over the Postgres wire protocol (psycopg 3).

The gateway authenticates the human; this client then connects to the engine as the trusted
service, naming the principal as the startup `user` and presenting the engine service secret as
the password (`SPEC.md` §10 A2). One short-lived connection per call: the engine is on localhost,
the handshake is cheap, and it means no session state (the per-session `privguard.rho`) can leak
from one principal's request into another's.

`psycopg.ClientCursor` binds parameters on the client and sends a plain simple query, the only
statement path the engine implements (`SPEC.md` §8). The analyst's SQL is passed as the query
text with no parameters, so nothing in it is ever interpolated by us.
"""
from __future__ import annotations

import json

import psycopg

from .engine import AuditRow, BudgetView, EngineError, QueryOutcome
from .store import Principal

#: SQLSTATE -> error class (`SPEC.md` §9).
_KIND_BY_SQLSTATE = {
    "42601": "parse",
    "42501": "policy",
    "53400": "budget",
    "42P01": "catalog",
}


class PgEngine:
    def __init__(
        self,
        host: str,
        port: int,
        secret: str,
        dbname: str = "safehealth",
        connect_timeout: int = 5,
    ) -> None:
        self._conninfo = {
            "host": host,
            "port": port,
            "password": secret,
            "dbname": dbname,
            "connect_timeout": connect_timeout,
        }

    # -- plumbing
    def _connect(self, principal: Principal) -> psycopg.Connection:
        try:
            return psycopg.connect(
                user=principal.username,
                autocommit=True,
                cursor_factory=psycopg.ClientCursor,
                **self._conninfo,
            )
        except psycopg.OperationalError as e:
            # Either the engine is down or it refused the credentials; both are the
            # gateway's problem, not something to show the analyst in detail.
            raise EngineError("unavailable", "the query engine is unavailable") from e

    def _translate(self, e: psycopg.Error) -> EngineError:
        state = getattr(e, "sqlstate", None)
        kind = _KIND_BY_SQLSTATE.get(state or "", "engine")
        message = e.diag.message_primary or str(e)
        # The engine prefixes its error class ("policy error: ..."); the HTTP status already
        # carries it, so keep just the message.
        if ": " in message and kind != "engine":
            message = message.split(": ", 1)[1]
        return EngineError(kind, message)

    def _rows(self, principal: Principal, sql: str) -> tuple[list[str], list[tuple]]:
        with self._connect(principal) as conn:
            try:
                cur = conn.execute(sql)
                columns = [c.name for c in cur.description or []]
                return columns, cur.fetchall()
            except psycopg.Error as e:
                raise self._translate(e) from e

    # -- EngineClient
    def query(self, principal: Principal, sql: str, rho: float) -> QueryOutcome:
        notices: list[str] = []
        with self._connect(principal) as conn:
            conn.add_notice_handler(lambda d: notices.append(d.message_primary or ""))
            try:
                conn.execute("SET privguard.rho = %s", (rho,))
                cur = conn.execute(sql)
                columns = [c.name for c in cur.description or []]
                rows = cur.fetchall()
            except psycopg.Error as e:
                raise self._translate(e) from e

        meta = next((json.loads(n) for n in notices if n.startswith("{")), None)
        if meta is None:
            raise EngineError("engine", "the engine returned no privacy metadata")
        return QueryOutcome(
            status=meta["status"],
            result=[dict(zip(columns, row)) for row in rows],
            rho_charged=meta["rho_charged"],
            epsilon_charged=meta["epsilon_charged"],
            mechanism=meta["mechanism"],
            query_id=meta["query_id"],
            suppressed_groups=meta["suppressed_groups"],
            budget=meta["budget"],
        )

    @staticmethod
    def _budget_view(row: tuple) -> BudgetView:
        account_id, name, total, spent, reserved, remaining, status = row
        return BudgetView(
            account_id=account_id,
            principal_name=name,
            rho_total=total,
            rho_spent=spent,
            rho_reserved=reserved,
            rho_remaining=remaining,
            delta_target=1e-6,
            window_type="lifetime",
            status=status,
        )

    def budget(self, principal: Principal) -> BudgetView | None:
        _, rows = self._rows(principal, "SHOW budget")
        return self._budget_view(rows[0]) if rows else None

    def budgets(self, principal: Principal) -> list[BudgetView]:
        _, rows = self._rows(principal, "SHOW budgets")
        return [self._budget_view(r) for r in rows]

    def verify_ledger(self, principal: Principal) -> dict | None:
        _, rows = self._rows(principal, "SHOW ledger")
        if not rows:
            return None
        ok, entries, first_bad = rows[0]
        return {"ok": ok == "t" or ok is True, "entries": entries, "first_bad_entry": first_bad}

    def audit(self, principal: Principal) -> list[AuditRow]:
        _, rows = self._rows(principal, "SHOW queries")
        return [
            AuditRow(
                query_id=qid,
                principal_name=name,
                status=status,
                rho_charged=rho,
                delta_charged=delta,
                latency_ms=None,  # the engine does not record latency
                sql_text=sql,
                submitted_at=ts.replace(" ", "T") + "Z",
            )
            for qid, name, status, rho, delta, ts, sql in rows
        ]

    def explain(self, principal: Principal, sql: str) -> dict:
        _, rows = self._rows(principal, f"EXPLAIN {sql}")
        return json.loads(rows[0][0])


