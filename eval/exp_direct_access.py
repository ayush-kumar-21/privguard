"""Experiment 4 — what can someone with *database* access (not API access) read?

Run through `manage.py shell` against the seeded database (see eval/run_all.sh).

Part A  The same attempts made as the application role (what a stolen app credential
        or a backup restore gives) and as an analyst login `analyst_<id>`.
Part B  The noise-seed attack: read the noise seed from sys_noise_cache, replay the
        noise, subtract it from the DP answer, and recover the exact count. Measured
        against the old behaviour (raw seed stored) and the new (commitment stored).
"""
from django.db import connection, transaction

from accounts.models import Principal
from audit.models import SysNoiseCache
from catalog.models import SysDatabase
from privacy import mechanisms
from privacy.mechanisms import KeyedRandom
from privacy.service import _resolve_policy, _seed, _true_aggregate, run_private_query
from privacy.sql import parse

alice = Principal.objects.get(username="alice")
frank = Principal.objects.get(username="frank")

ATTEMPTS = [
    ("read raw patients (incl. mrn)", "SELECT mrn FROM patients LIMIT 1"),
    ("read raw visits", "SELECT total_charge FROM visits LIMIT 1"),
    ("read exact per-dept aggregates (v_visit_summary)", "SELECT visit_count FROM v_visit_summary LIMIT 1"),
    ("read cached answers + seed material (sys_noise_cache)", "SELECT noise_seed FROM sys_noise_cache LIMIT 1"),
    ("read the budget ledger", "SELECT 1 FROM sys_budget_ledger LIMIT 1"),
    ("read user table (password hashes)", "SELECT password FROM sys_principal LIMIT 1"),
    ("read timing column of sys_query", "SELECT latency_ms FROM sys_query LIMIT 1"),
    ("read own query history", "SELECT query_id, status FROM sys_query"),
    ("read ANOTHER analyst's query history", None),  # filled per login below
    ("grant self more budget (UPDATE)", "UPDATE sys_budget_account SET rho_total = 1e9"),
    ("call sp_refill_budget", "CALL sp_refill_budget(1, 1000.0, NULL)"),
]


def attempt(cur, sql, params=None):
    sp = transaction.savepoint()
    try:
        cur.execute(sql, params)
        n = len(cur.fetchall()) if cur.description else cur.rowcount
        transaction.savepoint_commit(sp)
        return f"ALLOWED ({n} row{'s' if n != 1 else ''})"
    except Exception as err:  # noqa: BLE001 - classify any database refusal
        transaction.savepoint_rollback(sp)
        return "DENIED" if "permission denied" in str(err) else f"error: {str(err)[:40]}"


# Give the *other* analyst some history so row-level isolation is actually exercised.
for dept in (1, 2):
    run_private_query(frank, "safehealth", f"SELECT COUNT(*) FROM visits WHERE dept_id = {dept}",
                      rho_req=0.05)

print("\nEXP 4A  direct database access: application role vs analyst login")
print(f"{'attempt':<56}{'app role':<22}{'analyst login':<22}")
with transaction.atomic():
    cur = connection.cursor()
    login = f"analyst_{alice.id}"
    cur.execute(f"CREATE ROLE {login} LOGIN IN ROLE privguard_analyst")
    results = []
    for label, sql in ATTEMPTS:
        sql_app = sql or f"SELECT query_id FROM sys_query WHERE principal_id = {frank.id}"
        # Destructive rows are only attempted as the analyst (the app role would succeed).
        app = "ALLOWED" if label.startswith(("grant", "call")) else attempt(cur, sql_app)
        results.append((label, app, sql_app))
    cur.execute(f"SET LOCAL SESSION AUTHORIZATION {login}")
    for label, app, sql_app in results:
        print(f"{label:<56}{app:<22}{attempt(cur, sql_app):<22}")
    cur.execute("RESET SESSION AUTHORIZATION")
    transaction.set_rollback(True)  # leaves no role or change behind

# ------------------------------------------------------------------ Part B
print("\nEXP 4B  noise-seed attack: recover the exact count from the database alone")
db = SysDatabase.objects.get(db_name="safehealth")
SysNoiseCache.objects.filter(principal=alice).delete()
sql = "SELECT COUNT(*) FROM visits WHERE admission_type = 'EMERGENCY' AND dept_id = 3"
spec = parse(sql)
truth = _true_aggregate(spec, _resolve_policy(db, spec))["n"]
resp = run_private_query(alice, "safehealth", sql, rho_req=0.05)
answer = resp["result"][0]["value"]
stored = bytes(SysNoiseCache.objects.get(principal=alice).noise_seed)
raw_seed = _seed(spec.ast_hash(), alice.id)
C = 5  # contribution bound used by the COUNT mechanism
old = answer - mechanisms.noisy_laplace(0.0, C, 0.05, KeyedRandom(raw_seed))
new = answer - mechanisms.noisy_laplace(0.0, C, 0.05, KeyedRandom(stored))
print(f"  true count (ground truth)                    : {truth}")
print(f"  DP answer the analyst was given              : {answer}")
print(f"  attacker, OLD storage (raw seed in the table): recovers {old:g}   -> "
      f"{'EXACT' if old == truth else 'not exact'}")
print(f"  attacker, NEW storage (commitment only)      : recovers {new:g}   -> "
      f"{'EXACT' if new == truth else 'wrong'}")
