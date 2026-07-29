"""
The differentially-private query executor.

Pipeline for one analyst query:

  parse (sqlglot) -> fingerprint -> noise-cache lookup (memoisation)
    -> policy lookup (contribution bounds + mechanism + clamps)
    -> RESERVE budget (sp_reserve_budget, row-locked)
    -> contribution-bound + clamp + aggregate via the Django ORM
    -> add calibrated noise (+ DP partition selection for GROUP BY)
    -> SETTLE budget (sp_settle_budget) -> cache -> audit log (sys_query)

The budget is charged through the PL/pgSQL procedures from migration 0002, so a
query cannot reach data without passing the row-locked budget check first.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict

from django.conf import settings
from django.db import connection, transaction, IntegrityError
from django.db.models import Count, F, Q, Sum, Value, Window
from django.db.models.functions import Coalesce, DenseRank, Greatest, Least, RowNumber

from audit.models import SysNoiseCache, SysQuery, SysQueryFingerprint
from budget.models import BudgetAccount
from catalog.models import (
    SysColumn, SysColumnPrivacy, SysDatabase, SysTable, SysTablePrivacy,
)
from safehealth.models import Visit

from . import accounting, mechanisms
from .mechanisms import KeyedRandom, clamp
from .sql import DPQueryError, PRIVACY_UNIT, parse

DEFAULT_RHO = 0.05
DEFAULT_MAX_ROWS = 5       # contribution bound C (L-inf)
DEFAULT_MAX_GROUPS = 3     # L0 bound G
# DP partition-selection delta: a per-partition knob, chosen from the number of
# candidate partitions, NOT the tiny mechanism delta (else every group is dropped).
DELTA_PARTITION = 1e-2


def _server_secret() -> bytes:
    return getattr(settings, "PRIVGUARD_SERVER_SECRET", "dev-noise-secret").encode()


def _seed(ast_hash: bytes, principal_id: int, salt: bytes = b"") -> bytes:
    return hashlib.sha256(_server_secret() + ast_hash + principal_id.to_bytes(8, "big") + salt).digest()


def _build_q(filters) -> Q:
    q = Q()
    for col, op, val in filters:
        if op == "ne":
            q &= ~Q(**{col: val})
        else:
            q &= Q(**{f"{col}__{op}": val})
    return q


def _call_status(proc_sql: str, params: list) -> str:
    with connection.cursor() as cur:
        cur.execute(proc_sql, params)
        return cur.fetchone()[-1]


class Policy:
    def __init__(self, max_rows, max_groups, mechanism, lo, hi):
        self.max_rows = max_rows
        self.max_groups = max_groups
        self.mechanism = mechanism
        self.lo = lo
        self.hi = hi

    @property
    def clamp_magnitude(self) -> float:
        return max(abs(self.lo or 0.0), abs(self.hi or 0.0)) or 1.0


def _resolve_policy(db: SysDatabase, spec) -> Policy:
    max_rows, max_groups = DEFAULT_MAX_ROWS, DEFAULT_MAX_GROUPS
    table = SysTable.objects.filter(db=db, table_name=spec.table).first()
    if table:
        tp = SysTablePrivacy.objects.filter(table=table).first()
        if tp:
            max_rows, max_groups = tp.max_rows_per_unit, tp.max_groups_per_unit

    mechanism = "discrete_laplace" if spec.agg == "COUNT" else "laplace"
    lo = hi = None
    if spec.column:
        col = SysColumn.objects.filter(table=table, col_name=spec.column).first() if table else None
        cp = SysColumnPrivacy.objects.filter(column=col).first() if col else None
        if cp:
            mechanism, lo, hi = cp.mechanism, cp.lower_clamp, cp.upper_clamp
        if lo is None or hi is None:
            raise DPQueryError(
                f"{spec.agg}({spec.column}) needs a column privacy policy with clamp bounds"
            )
    return Policy(max_rows, max_groups, mechanism, lo, hi)


def _noise(mechanism: str, rho: float, delta1: float, delta2: float, rng: KeyedRandom) -> float:
    if mechanism == "gaussian":
        return mechanics_gaussian(delta2, rho, rng)
    if mechanism == "discrete_laplace":
        return mechanisms.discrete_laplace(accounting.laplace_scale_from_rho(delta1, rho), rng)
    return mechanisms.laplace(accounting.laplace_scale_from_rho(delta1, rho), rng)


def mechanics_gaussian(delta2, rho, rng):
    return mechanisms.gaussian(accounting.sigma_from_rho(delta2, rho), rng)


# --------------------------------------------------------------------------
# SQL-pushdown: contribution bounding + aggregation in PostgreSQL
# --------------------------------------------------------------------------
def _bounded_rows(spec, policy: Policy):
    """One queryset that applies the L-inf and L0 contribution bounds in SQL.

    Uses Window functions (ROW_NUMBER / DENSE_RANK) so the database does the
    bounding — Python receives only the survivors, not every row.
    """
    qs = Visit.objects.filter(_build_q(spec.filters))
    partition = [F(PRIVACY_UNIT)]
    if spec.group_by:
        qs = qs.annotate(grp_rank=Window(
            expression=DenseRank(),
            partition_by=partition,
            order_by=F(spec.group_by).asc(),
        )).filter(grp_rank__lte=policy.max_groups)          # L0 bound (G)
        partition = partition + [F(spec.group_by)]
    return qs.annotate(rn=Window(
        expression=RowNumber(),
        partition_by=partition,
        order_by=F("visit_id").asc(),
    )).filter(rn__lte=policy.max_rows)                      # L-inf bound (C)


def _true_aggregate(spec, policy: Policy):
    """Returns the *unnoised* aggregate(s). The only DB round trip.

    All literals are bound parameters (ORM expressions), so the SQLi posture
    is unchanged — analyst SQL never reaches PostgreSQL as SQL.
    """
    rows = _bounded_rows(spec, policy)
    clamped = Least(Greatest(Coalesce(F(spec.column), Value(0.0)),
                             Value(float(policy.lo))),
                    Value(float(policy.hi))) if spec.column else None
    aggs = {"n": Count("visit_id")}
    if spec.column:
        aggs["total"] = Sum(clamped)
    if spec.group_by:
        aggs["units"] = Count(PRIVACY_UNIT, distinct=True)
        return list(rows.values(spec.group_by).annotate(**aggs).order_by(spec.group_by))
    return rows.aggregate(**aggs)


# --------------------------------------------------------------------------
# Scalar and grouped execution
# --------------------------------------------------------------------------
def _execute_scalar(spec, policy: Policy, rho: float, rng: KeyedRandom):
    """Scalar (non-grouped) DP aggregate using SQL pushdown."""
    agg = _true_aggregate(spec, policy)
    true_count = agg["n"]
    C, M = policy.max_rows, policy.clamp_magnitude

    if spec.agg == "COUNT":
        delta_sens = float(C)
        value = true_count + _noise(policy.mechanism, rho, delta_sens, delta_sens, rng)
        return [{"value": round(value, 4)}], delta_sens, true_count
    if spec.agg == "SUM":
        delta_sens = C * M
        true_sum = agg.get("total", 0.0) or 0.0
        value = true_sum + _noise(policy.mechanism, rho, delta_sens, delta_sens, rng)
        return [{"value": round(value, 4)}], delta_sens, true_count
    # AVG = noisy_sum / max(noisy_count, 1), budget split 50/50
    rho_s, rho_c = rho / 2.0, rho / 2.0
    true_sum = agg.get("total", 0.0) or 0.0
    noisy_sum = true_sum + _noise(policy.mechanism, rho_s, C * M, C * M, rng)
    noisy_cnt = true_count + _noise(policy.mechanism, rho_c, C, C, rng)
    return [{"value": round(noisy_sum / max(noisy_cnt, 1.0), 4)}], C * M, true_count


def _execute_grouped(spec, policy: Policy, rho: float, seed: bytes, delta_partition: float = DELTA_PARTITION):
    """Grouped DP aggregate using SQL pushdown + DP partition selection."""
    if spec.agg == "AVG":
        raise DPQueryError("AVG with GROUP BY is not supported in this version")
    C, G, M = policy.max_rows, policy.max_groups, policy.clamp_magnitude

    group_rows = _true_aggregate(spec, policy)
    total_rows_scanned = sum(r["n"] for r in group_rows)

    # DP partition selection (spend rho_part), then noise the survivors (rho_agg).
    rho_part, rho_agg = rho * 0.2, rho * 0.8
    eps_part = math.sqrt(2.0 * rho_part)
    scale_part = 1.0 / eps_part
    tau = 1.0 + scale_part * math.log(1.0 / (2.0 * delta_partition))

    part_rng = KeyedRandom(seed + b"partition")
    agg_rng = KeyedRandom(seed + b"aggregate")
    per_sens = M if spec.agg == "SUM" else 1.0
    delta2 = math.sqrt(G) * C * per_sens

    results, suppressed = [], 0
    for r in group_rows:
        noisy_c = r["units"] + mechanisms.laplace(scale_part, part_rng)
        if noisy_c <= tau:
            suppressed += 1
            continue
        true_val = (r.get("total", 0.0) or 0.0) if spec.agg == "SUM" else r["n"]
        noisy_val = true_val + mechanics_gaussian(delta2, rho_agg, agg_rng)
        results.append({spec.group_by: r[spec.group_by], "value": round(noisy_val, 4)})
    return results, suppressed, delta2, total_rows_scanned


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def run_private_query(principal, db_name: str, sql: str, rho_req: float = DEFAULT_RHO) -> dict:
    db = SysDatabase.objects.filter(db_name=db_name).first()
    if not db:
        raise DPQueryError(f"unknown database: {db_name}")
    account = BudgetAccount.objects.filter(principal=principal, db=db).first()
    if not account:
        raise DPQueryError("no budget account for this principal/database")

    spec = parse(sql)
    ast_hash = spec.ast_hash()
    try:
        fingerprint, _ = SysQueryFingerprint.objects.get_or_create(
            ast_hash=ast_hash,
            defaults={
                "table": SysTable.objects.filter(db=db, table_name=spec.table).first()
                or _ensure_fact_table(db, spec.table),
                "agg_kind": spec.agg,
                "predicate_sig": spec.canonical()[:1024],
                "groupby_sig": spec.group_by,
            },
        )
    except IntegrityError:
        fingerprint = SysQueryFingerprint.objects.get(ast_hash=ast_hash)

    # --- Memoisation: identical fingerprint + principal -> cached answer, rho=0
    cached = SysNoiseCache.objects.filter(fingerprint=fingerprint, principal=principal).first()
    if cached:
        q = SysQuery.objects.create(
            principal=principal, db=db, fingerprint=fingerprint, sql_text=sql,
            status="cache_hit", rho_charged=0.0, latency_ms=0.0,
        )
        # `cached_result` became a JSONField in audit.0002, so it now hands back
        # the decoded rows. Rows written before that migration are still stored
        # as a JSON string, so decode those rather than orphaning them: a stale
        # cache entry would otherwise 500 every repeat of that query.
        cached_rows = cached.cached_result
        if isinstance(cached_rows, (str, bytes, bytearray)):
            cached_rows = json.loads(cached_rows)

        return _response("cache_hit", cached_rows, 0.0, 0.0,
                         "cache", q.query_id, account)

    policy = _resolve_policy(db, spec)
    delta_target = account.delta_target or db.default_delta
    eps_equiv = accounting.eps_from_rho(rho_req, delta_target)
    seed = _seed(ast_hash, principal.id)

    q = SysQuery.objects.create(
        principal=principal, db=db, fingerprint=fingerprint, sql_text=sql,
        status="pending", rho_charged=0.0,
    )

    started = time.perf_counter()

    # representative sensitivity/scale for the ledger row
    C, M = policy.max_rows, policy.clamp_magnitude
    sens = float(C) if spec.agg == "COUNT" else C * M
    scale = (accounting.sigma_from_rho(sens, rho_req) if policy.mechanism == "gaussian"
             else accounting.laplace_scale_from_rho(sens, rho_req))

    status = _call_status(
        "CALL sp_reserve_budget(%s,%s,%s,%s,%s,%s,%s,%s)",
        [account.account_id, q.query_id, rho_req, policy.mechanism, sens, scale, eps_equiv, None],
    )
    if status != "reserved":
        q.status = "rejected_budget"
        q.save(update_fields=["status"])
        account.refresh_from_db()
        return _response("rejected_budget", [], 0.0, 0.0, policy.mechanism, q.query_id, account)

    try:
        if spec.group_by:
            results, suppressed, _, rows_scanned = _execute_grouped(spec, policy, rho_req, seed)
        else:
            rng = KeyedRandom(seed)
            results, _, rows_scanned = _execute_scalar(spec, policy, rho_req, rng)
            suppressed = 0
    except DPQueryError:
        # Policy error: abort and re-raise for the API to map to 400
        _call_status("CALL sp_settle_budget(%s,%s,%s,%s,%s,%s,%s)",
                     [account.account_id, q.query_id, rho_req, False, policy.mechanism, eps_equiv, None])
        q.status = "rejected_policy"
        q.save(update_fields=["status"])
        raise
    except Exception:
        # Unexpected error: abort and mark as error
        _call_status("CALL sp_settle_budget(%s,%s,%s,%s,%s,%s,%s)",
                     [account.account_id, q.query_id, rho_req, False, policy.mechanism, eps_equiv, None])
        q.status = "error"
        q.save(update_fields=["status"])
        raise

    with transaction.atomic():
        _call_status("CALL sp_settle_budget(%s,%s,%s,%s,%s,%s,%s)",
                     [account.account_id, q.query_id, rho_req, True, policy.mechanism, eps_equiv, None])

        SysNoiseCache.objects.create(
            fingerprint=fingerprint, principal=principal, noise_seed=seed,
            # JSONField encodes for us; json.dumps here would double-encode.
            cached_result=results, rho_charged=rho_req,
        )
        # F() makes this an atomic DB increment — fixes the lost-update race
        # where two concurrent identical queries both read the same times_seen.
        fingerprint.times_seen = F("times_seen") + 1
        fingerprint.save(update_fields=["times_seen"])

        q.status = "executed"
        q.rho_charged = rho_req
        q.latency_ms = (time.perf_counter() - started) * 1000.0
        q.rows_scanned = rows_scanned
        q.save(update_fields=["status", "rho_charged", "latency_ms", "rows_scanned"])

    account.refresh_from_db()
    resp = _response("executed", results, rho_req, eps_equiv, policy.mechanism, q.query_id, account)
    if spec.group_by:
        resp["suppressed_groups"] = suppressed
    return resp


def _ensure_fact_table(db: SysDatabase, name: str) -> SysTable:
    """Look up a fact table or raise — catalog seeding belongs in seed_demo/migrations."""
    table = SysTable.objects.filter(db=db, table_name=name).first()
    if table:
        return table
    raise DPQueryError(f"Table '{name}' not found in catalog for database '{db.db_name}'. "
                       f"Run seed_demo or add a catalog migration.")


def _response(status, result, rho, eps, mechanism, query_id, account) -> dict:
    return {
        "status": status,
        "result": result,
        "rho_charged": round(rho, 6),
        "epsilon_charged": round(eps, 6),
        "mechanism": mechanism,
        "query_id": query_id,
        "budget": {
            "rho_total": account.rho_total,
            "rho_spent": account.rho_spent,
            "rho_reserved": account.rho_reserved,
            "rho_remaining": account.rho_remaining,
            "status": account.status,
        },
    }
