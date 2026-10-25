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


def seed_commitment(seed: bytes) -> bytes:
    """One-way commitment stored in sys_noise_cache instead of the seed.

    The seed reproduces the noise exactly (answer - noise = true value), so it
    must never be persisted: it would sit in the table and in every pg_dump.
    """
    return hashlib.sha256(b"privguard/seed-commitment/v1" + seed).digest()


def partition_params(rho_part: float, groups: int, delta: float):
    """Noise parameter and threshold for DP partition selection.

    A unit lands in up to `groups` (= G, the L0 bound) partitions, so it
    influences G independent releases. Each is eps0-DP, and G of them compose
    (additively in zCDP) to `rho_part` when eps0 = sqrt(2 * rho_part / G). The
    earlier code used eps0 = sqrt(2 * rho_part) for every partition, understating
    this stage's cost by a factor of G (cf. Wilson et al., PoPETs 2020, Thm 2).

    delta: a partition holding a single unit must be released with probability
    <= delta_p, and a unit touches G partitions, so delta_p = 1-(1-delta)^(1/G).
    The discrete Laplace tail is P(Z >= k) = e^(-eps0*k) / (1 + e^(-eps0)).

    Returns (eps_fraction, tau): release a partition iff noisy_count > tau.
    Accounting is approximate (rho, delta): the eps part is charged to the rho
    ledger, delta is recorded separately in sys_query.delta_charged.
    """
    eps = mechanisms.conservative_epsilon(math.sqrt(2.0 * rho_part / groups))
    e0 = float(eps)
    delta_p = -math.expm1(math.log1p(-delta) / groups)
    tau = 1.0 + math.log(1.0 / (delta_p * (1.0 + math.exp(-e0)))) / e0
    return eps, tau


def _build_q(filters) -> Q:
    q = Q()
    for col, op, val in filters:
        if op == "ne":
            q &= ~Q(**{col: val})
        else:
            q &= Q(**{f"{col}__{op}": val})
    return q


def _contributors(spec) -> list:
    """Patients with at least one row that survives the query's filters (the ones it can affect)."""
    return list(Visit.objects.filter(_build_q(spec.filters))
                .values_list(PRIVACY_UNIT, flat=True).distinct())


def _charge_patients(query_id: int, rho: float, patient_ids: list) -> list:
    """Charge `rho` to each contributing patient who can afford it; returns the ids left out."""
    if not patient_ids:
        return []
    with connection.cursor() as cur:
        cur.execute("SELECT fn_charge_patients(%s, %s, %s::integer[])", [query_id, rho, patient_ids])
        return list(cur.fetchone()[0] or [])


def _refund_patients(query_id: int) -> None:
    """Give back a query's patient charges (it produced no answer). Idempotent."""
    with connection.cursor() as cur:
        cur.execute("SELECT fn_refund_patients(%s)", [query_id])


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
        # Patients left out of this query because their own budget cannot cover it.
        self.excluded: frozenset = frozenset()

    @property
    def clamp_magnitude(self) -> float:
        return max(abs(self.lo or 0.0), abs(self.hi or 0.0)) or 1.0

    @property
    def center(self) -> float:
        """Midpoint of the clamp interval (the shift used by the centred mean)."""
        return ((self.lo or 0.0) + (self.hi or 0.0)) / 2.0

    @property
    def half_range(self) -> float:
        """Half the width of the clamp interval: the per-row sensitivity of a centred sum."""
        return ((self.hi or 0.0) - (self.lo or 0.0)) / 2.0


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


def _noisy(mechanism: str, true_value: float, rho: float, delta1: float, delta2: float,
           rng: KeyedRandom, grid: float = 1.0) -> float:
    """Release `true_value` under rho-zCDP using exact integer-grid samplers.

    `grid` is 1 for counts and SUM_GRID for real-valued sums (see mechanisms.py).
    """
    if mechanism == "gaussian":
        return mechanisms.noisy_gaussian(true_value, delta2, rho, rng, grid)
    return mechanisms.noisy_laplace(true_value, delta1, rho, rng, grid)


# --------------------------------------------------------------------------
# SQL-pushdown: contribution bounding + aggregation in PostgreSQL
# --------------------------------------------------------------------------
def _bounded_rows(spec, policy: Policy):
    """One queryset that applies the L-inf and L0 contribution bounds in SQL.

    Uses Window functions (ROW_NUMBER / DENSE_RANK) so the database does the
    bounding — Python receives only the survivors, not every row.
    """
    qs = Visit.objects.filter(_build_q(spec.filters))
    if policy.excluded:
        qs = qs.exclude(**{f"{PRIVACY_UNIT}__in": policy.excluded})  # budget-exhausted patients
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
    # Aggregate over the bounded rows through a subquery on their ids.
    # Aggregating the window-annotated queryset directly makes Django GROUP BY
    # every annotation, which yields one group per row (n=1, units=1) — every
    # group is then suppressed and GROUP BY queries always return nothing.
    rows = Visit.objects.filter(visit_id__in=_bounded_rows(spec, policy).values("visit_id"))
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
        value = _noisy(policy.mechanism, true_count, rho, delta_sens, delta_sens, rng)
        return [{"value": round(value, 4)}], delta_sens, true_count
    if spec.agg == "SUM":
        delta_sens = C * M
        true_sum = agg.get("total", 0.0) or 0.0
        value = _noisy(policy.mechanism, true_sum, rho, delta_sens, delta_sens, rng,
                       grid=mechanisms.SUM_GRID)
        return [{"value": round(value, 4)}], delta_sens, true_count
    # AVG = centre + noisy_centred_sum / max(noisy_count, 1), budget split 50/50.
    #
    # Centred mean: noise the sum of (x - c) with c the midpoint of the clamp interval, instead
    # of the sum of x. Adding or removing one patient changes the centred sum by at most
    # C * (hi - lo) / 2 (each of their <= C rows contributes x - c in [-h, h]), against C * M
    # for the plain sum, so the same rho buys noise that is up to M / h times smaller (2x for
    # a clamp of [0, hi]). The count is noised separately as before; c + S'/N' is
    # post-processing, so the guarantee is unchanged.
    rho_s, rho_c = rho / 2.0, rho / 2.0
    h, centre = policy.half_range, policy.center
    centred_sum = (agg.get("total", 0.0) or 0.0) - centre * true_count
    noisy_sum = _noisy(policy.mechanism, centred_sum, rho_s, C * h, C * h, rng,
                       grid=mechanisms.SUM_GRID)
    noisy_cnt = _noisy(policy.mechanism, true_count, rho_c, C, C, rng)
    return [{"value": round(centre + noisy_sum / max(noisy_cnt, 1.0), 4)}], C * h, true_count


def _execute_grouped(spec, policy: Policy, rho: float, seed: bytes, delta_partition: float = DELTA_PARTITION):
    """Grouped DP aggregate using SQL pushdown + DP partition selection."""
    if spec.agg == "AVG":
        raise DPQueryError("AVG with GROUP BY is not supported in this version")
    C, G, M = policy.max_rows, policy.max_groups, policy.clamp_magnitude

    group_rows = _true_aggregate(spec, policy)
    total_rows_scanned = sum(r["n"] for r in group_rows)

    # DP partition selection (spend rho_part), then noise the survivors (rho_agg).
    rho_part, rho_agg = rho * 0.2, rho * 0.8
    eps_part, tau = partition_params(rho_part, G, delta_partition)

    part_rng = KeyedRandom(seed + b"partition")
    agg_rng = KeyedRandom(seed + b"aggregate")
    per_sens = M if spec.agg == "SUM" else 1.0
    delta2 = math.sqrt(G) * C * per_sens

    results, suppressed = [], 0
    for r in group_rows:
        noisy_c = r["units"] + mechanisms.sample_discrete_laplace(
            eps_part.numerator, eps_part.denominator, part_rng)
        if noisy_c <= tau:
            suppressed += 1
            continue
        true_val = (r.get("total", 0.0) or 0.0) if spec.agg == "SUM" else r["n"]
        noisy_val = _noisy("gaussian", true_val, rho_agg, delta2, delta2, agg_rng,
                           grid=mechanisms.SUM_GRID if spec.agg == "SUM" else 1.0)
        results.append({spec.group_by: r[spec.group_by], "value": round(noisy_val, 4)})
    return results, suppressed, delta2, total_rows_scanned


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def _get_fingerprint(db, spec, ast_hash):
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
    return fingerprint


def padded_duration(elapsed: float, floor: float) -> float:
    """How long a query response should take in total, to hide how much work it did.

    Up to the floor every response takes exactly the floor. A query that runs longer (many patients
    charged, a big scan) is rounded up to the next doubling of the floor (floor, 2x, 4x, 8x ...),
    so an observer learns only which bucket it fell in, not the exact duration.
    """
    if floor <= 0:
        return 0.0
    if elapsed <= floor:
        return floor
    return floor * 2 ** math.ceil(math.log2(elapsed / floor))


def _take_run(principal, db_name: str):
    """Use one run of the principal's quota, atomically. Returns the account id once a run was
    taken, `"no_account"` when there is no quota to charge, or None when the quota is spent.

    One conditional UPDATE does the check and the increment together, so two concurrent queries
    can never both take the last run, and `max_queries` is read from the row at that moment:
    an administrator's change applies to the very next query.
    """
    account_id = (
        BudgetAccount.objects.filter(principal=principal, db__db_name=db_name)
        .values_list("account_id", flat=True).first()
    )
    if account_id is None:
        return "no_account"  # no account: the normal "no budget account" path reports it
    taken = BudgetAccount.objects.filter(
        Q(max_queries__isnull=True) | Q(queries_used__lt=F("max_queries")),
        account_id=account_id,
    ).update(queries_used=F("queries_used") + 1)
    return account_id if taken else None


def _refund_run(account_id) -> None:
    BudgetAccount.objects.filter(account_id=account_id, queries_used__gt=0).update(
        queries_used=F("queries_used") - 1
    )


def _quota_exhausted(principal, db_name: str, sql: str) -> dict:
    """The answer for a spent quota. Audited like any other refusal, so monitoring sees it."""
    db = SysDatabase.objects.get(db_name=db_name)
    account = BudgetAccount.objects.get(principal=principal, db=db)
    spec = parse(sql)  # a malformed query is a 400 first, whatever the quota says
    SysQuery.objects.create(
        principal=principal, db=db, fingerprint=_get_fingerprint(db, spec, spec.ast_hash()),
        sql_text=sql, status="rejected_budget", rho_charged=0.0, latency_ms=0.0,
    )
    resp = _response("rejected_budget", [], 0.0, 0.0, "none", None, account)
    resp["reason"] = "quota"
    return resp


def run_private_query(principal, db_name: str, sql: str, rho_req: float = DEFAULT_RHO) -> dict:
    """Run a DP query, padding the response time to a fixed floor.

    The exact samplers loop a randomised number of times, so without a floor the
    wall-clock time is a (weak) side channel (Jin et al., IEEE S&P 2022).

    Each answered query uses one run of the analyst's quota; anything that does not produce an
    answer (rejection, policy error, crash) gives the run back.
    """
    started = time.perf_counter()
    taken = None
    try:
        # Separation of duty: the per-patient ledger shows exactly who a query touched, and an
        # administrator may read it, so an administrator must not be able to generate queries.
        if principal.is_superuser or principal.has_role("dba"):
            raise DPQueryError("administrators cannot run queries (separation of duty); use an analyst account")
        taken = _take_run(principal, db_name)
        if taken is None:
            return _quota_exhausted(principal, db_name, sql)
        result = _run_private_query(principal, db_name, sql, rho_req)
        if taken != "no_account":
            if result["status"] not in ("executed", "cache_hit"):
                _refund_run(taken)
            result = _with_quota(result, taken)
        return result
    except BaseException:
        if taken not in (None, "no_account"):
            _refund_run(taken)
        raise
    finally:
        floor = float(getattr(settings, "PRIVGUARD_MIN_RESPONSE_MS", 0)) / 1000.0
        remaining = padded_duration(time.perf_counter() - started, floor) - (time.perf_counter() - started)
        if remaining > 0:
            time.sleep(remaining)


def _with_quota(result: dict, account_id) -> dict:
    """Refresh the quota figures in `result["budget"]` after the run was settled."""
    account = BudgetAccount.objects.get(account_id=account_id)
    result["budget"].update(_quota_fields(account))
    return result


def _run_private_query(principal, db_name: str, sql: str, rho_req: float = DEFAULT_RHO) -> dict:
    db = SysDatabase.objects.filter(db_name=db_name).first()
    if not db:
        raise DPQueryError(f"unknown database: {db_name}")
    account = BudgetAccount.objects.filter(principal=principal, db=db).first()
    if not account:
        raise DPQueryError("no budget account for this principal/database")

    spec = parse(sql)
    ast_hash = spec.ast_hash()
    fingerprint = _get_fingerprint(db, spec, ast_hash)

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
    sens = float(C) if spec.agg == "COUNT" else (C * policy.half_range if spec.agg == "AVG" else C * M)
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
        resp = _response("rejected_budget", [], 0.0, 0.0, policy.mechanism, q.query_id, account)
        # Distinguish the per-analyst budget from the database-wide cap.
        resp["reason"] = "global_cap" if status == "rejected_global_cap" else status
        return resp

    try:
        # Per-patient budget: charge the patients this query can affect; any whose own allowance
        # is spent are left out of it. Analysts are told nothing about who was left out.
        policy.excluded = frozenset(_charge_patients(q.query_id, rho_req, _contributors(spec)))
        if spec.group_by:
            results, suppressed, _, _ = _execute_grouped(spec, policy, rho_req, seed)
        else:
            rng = KeyedRandom(seed)
            results, _, _ = _execute_scalar(spec, policy, rho_req, rng)
            suppressed = 0
    except DPQueryError:
        # Policy error: abort and re-raise for the API to map to 400
        _refund_patients(q.query_id)
        _call_status("CALL sp_settle_budget(%s,%s,%s,%s,%s,%s,%s)",
                     [account.account_id, q.query_id, rho_req, False, policy.mechanism, eps_equiv, None])
        q.status = "rejected_policy"
        q.save(update_fields=["status"])
        raise
    except Exception:
        # Unexpected error: abort and mark as error
        _refund_patients(q.query_id)
        _call_status("CALL sp_settle_budget(%s,%s,%s,%s,%s,%s,%s)",
                     [account.account_id, q.query_id, rho_req, False, policy.mechanism, eps_equiv, None])
        q.status = "error"
        q.save(update_fields=["status"])
        raise

    with transaction.atomic():
        _call_status("CALL sp_settle_budget(%s,%s,%s,%s,%s,%s,%s)",
                     [account.account_id, q.query_id, rho_req, True, policy.mechanism, eps_equiv, None])

        SysNoiseCache.objects.create(
            fingerprint=fingerprint, principal=principal, noise_seed=seed_commitment(seed),
            # JSONField encodes for us; json.dumps here would double-encode.
            cached_result=results, rho_charged=rho_req,
        )
        # F() makes this an atomic DB increment — fixes the lost-update race
        # where two concurrent identical queries both read the same times_seen.
        fingerprint.times_seen = F("times_seen") + 1
        fingerprint.save(update_fields=["times_seen"])

        q.status = "executed"
        q.rho_charged = rho_req
        # delta is spent only by DP partition selection (GROUP BY); it is outside
        # the rho ledger, so it is recorded per query and summed per account.
        q.delta_charged = DELTA_PARTITION if spec.group_by else 0.0
        q.latency_ms = (time.perf_counter() - started) * 1000.0
        # The exact row count is deliberately NOT persisted: for a COUNT it is the
        # un-noised answer, and anything stored here reaches views and backups.
        q.save(update_fields=["status", "rho_charged", "delta_charged", "latency_ms"])

    account.refresh_from_db()
    resp = _response("executed", results, rho_req, eps_equiv, policy.mechanism, q.query_id, account)
    resp["delta_charged"] = q.delta_charged
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
            **_quota_fields(account),
        },
    }


def _quota_fields(account) -> dict:
    return {
        "max_queries": account.max_queries,
        "queries_used": account.queries_used,
        "queries_remaining": account.queries_remaining,
    }
