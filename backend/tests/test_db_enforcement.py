"""Database-level enforcement: what an analyst can reach *without* going through the API.

The gateway runs as the table owner. `privguard_analyst` models everything else that
might hold analyst credentials (a BI connection, a leaked read-only connection string,
a SQL injection in a read path). Every test below is a direct attempt a malicious
analyst could make against PostgreSQL itself.
"""
import pytest
from django.db import connection, transaction
from django.db.utils import ProgrammingError

from accounts.models import Principal
from audit.models import SysNoiseCache, SysQuery
from budget.models import BudgetAccount
from catalog.models import SysTable
from privacy import mechanisms
from privacy.mechanisms import KeyedRandom
from privacy.service import _seed, run_private_query, seed_commitment
from privacy.sql import parse

ROLE = "privguard_analyst"

# Tables an analyst-facing connection must never be able to read.
FORBIDDEN_TABLES = [
    "patients", "visits", "diagnoses", "lab_results", "staff",  # raw clinical data
    "sys_noise_cache",                                          # cached answers + seed commitments
    "sys_budget_ledger", "sys_principal", "sys_column_grant",   # ledger, identities, policy
    "sys_query_fingerprint", "sys_noise_cache", "sys_database",
    "sys_patient_budget", "sys_patient_ledger",                 # which patients each query touched
]
FORBIDDEN_VIEWS = ["v_visit_summary", "v_patient_budget"]  # exact (un-noised) per-department aggregates


@pytest.fixture(autouse=True)
def require_role(db):
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [ROLE])
        if not cur.fetchone():
            pytest.skip(f"role {ROLE} not created (managed database without CREATE ROLE)")
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        if not cur.fetchone()[0]:
            pytest.skip("needs a superuser connection to SET SESSION AUTHORIZATION")


class AsAnalyst:
    """Act as an analyst who logged in as the role `analyst_<id>`, inside the test's transaction.

    `principal_id=None` models a login whose name does not encode an id.
    """

    def __init__(self, principal_id=None):
        self.principal_id = principal_id

    def __enter__(self):
        self.cur = connection.cursor()
        self.login = f"analyst_{self.principal_id}" if self.principal_id is not None else "analyst_unknown"
        # CREATE ROLE is transactional, so it is rolled back with the test.
        self.cur.execute(f"CREATE ROLE {self.login} LOGIN IN ROLE {ROLE}")
        self.cur.execute(f"SET LOCAL SESSION AUTHORIZATION {self.login}")
        return self

    def __exit__(self, *exc):
        self.cur.execute("RESET SESSION AUTHORIZATION")
        self.cur.close()

    def denied(self, sql, params=None):
        """True if PostgreSQL refuses the statement for lack of privilege."""
        try:
            with transaction.atomic():  # savepoint, so a failure does not poison the test
                self.cur.execute(sql, params)
                self.cur.fetchall()
        except ProgrammingError as err:
            return "permission denied" in str(err)
        return False

    def rows(self, sql, params=None):
        self.cur.execute(sql, params)
        return self.cur.fetchall()


def _query_for(principal, db):
    return SysQuery.objects.create(
        principal=principal, db=db, sql_text="SELECT COUNT(*) FROM visits",
        status="executed", rho_charged=0.05,
    )


@pytest.mark.django_db
class TestRawDataIsUnreachable:
    @pytest.mark.parametrize("table", sorted(set(FORBIDDEN_TABLES)))
    def test_cannot_read_table(self, table):
        with AsAnalyst() as a:
            assert a.denied(f"SELECT 1 FROM {table} LIMIT 1"), f"{table} is readable"

    @pytest.mark.parametrize("view", FORBIDDEN_VIEWS)
    def test_cannot_read_exact_aggregate_views(self, view):
        with AsAnalyst() as a:
            assert a.denied(f"SELECT 1 FROM {view} LIMIT 1")

    def test_cannot_read_timing_column(self):
        with AsAnalyst(1) as a:
            assert a.denied("SELECT latency_ms FROM sys_query")
            assert a.denied("SELECT * FROM sys_query")  # star would include latency_ms / plan_json

    def test_cannot_write_anything(self, analyst, account):
        q = _query_for(analyst, account.db)
        with AsAnalyst(analyst.id) as a:
            assert a.denied("DELETE FROM sys_query WHERE query_id = %s", [q.query_id])
            assert a.denied("UPDATE sys_budget_account SET rho_total = 1e9")
            assert a.denied(
                "INSERT INTO sys_query(principal_id, db_id, sql_text, status, rho_charged, delta_charged) "
                "VALUES (%s, %s, 'x', 'executed', 0, 0)", [analyst.id, account.db_id])

    def test_cannot_escalate_budget(self, analyst, account):
        with AsAnalyst(analyst.id) as a:
            assert a.denied("UPDATE sys_budget_account SET rho_spent = 0")
            assert a.denied("CALL sp_refill_budget(%s, 1000.0, NULL)", [account.account_id])


@pytest.mark.django_db
class TestRowLevelSecurity:
    @pytest.fixture
    def two_analysts(self, test_db):
        people = []
        for name in ("rls_a", "rls_b"):
            user = Principal.objects.create_user(username=name, password="x")
            BudgetAccount.objects.create(principal=user, db=test_db, rho_total=5.0, delta_target=1e-6)
            _query_for(user, test_db)
            _query_for(user, test_db)
            people.append(user)
        return people

    def test_sees_only_own_queries(self, two_analysts):
        a, b = two_analysts
        with AsAnalyst(a.id) as s:
            owners = {r[0] for r in s.rows("SELECT principal_id FROM sys_query")}
        assert owners == {a.id}
        with AsAnalyst(b.id) as s:
            owners = {r[0] for r in s.rows("SELECT principal_id FROM sys_query")}
        assert owners == {b.id}

    def test_sees_only_own_budget(self, two_analysts):
        a, _ = two_analysts
        with AsAnalyst(a.id) as s:
            owners = {r[0] for r in s.rows("SELECT principal_id FROM sys_budget_account")}
        assert owners == {a.id}

    def test_unidentified_session_sees_nothing(self, two_analysts):
        # Default deny: no app.principal_id means the policy matches no row.
        with AsAnalyst() as s:
            assert s.rows("SELECT query_id FROM sys_query") == []
            assert s.rows("SELECT account_id FROM sys_budget_account") == []

    def test_cannot_impersonate_by_filtering(self, two_analysts):
        a, b = two_analysts
        with AsAnalyst(a.id) as s:
            rows = s.rows("SELECT query_id FROM sys_query WHERE principal_id = %s", [b.id])
        assert rows == []

    def test_setting_a_session_variable_does_not_help(self, two_analysts):
        # An earlier design keyed RLS on `app.principal_id`; a client could just SET it.
        a, b = two_analysts
        with AsAnalyst(a.id) as s:
            s.cur.execute("SELECT set_config('app.principal_id', %s, false)", [str(b.id)])
            owners = {r[0] for r in s.rows("SELECT principal_id FROM sys_query")}
        assert owners == {a.id}

    # Not tested here: that a real analyst cannot `SET SESSION AUTHORIZATION` to another login.
    # PostgreSQL only forbids it when the *original* login was not a superuser, and these tests
    # simulate the login from the superuser test connection, so they cannot demonstrate it.

    def test_application_role_is_unaffected(self, two_analysts):
        # The gateway (table owner / superuser) still sees every row.
        assert SysQuery.objects.count() == 4
        assert BudgetAccount.objects.count() == 2


@pytest.mark.django_db
class TestNoiseSeedIsNeverStored:
    """A stored seed regenerates the noise exactly, so answer - noise = the true value."""

    def test_cache_holds_a_commitment_not_the_seed(self, analyst, account, visits, settings):
        settings.PRIVGUARD_MIN_RESPONSE_MS = 0
        SysTable.objects.create(db=account.db, table_name="visits")
        sql = "SELECT COUNT(*) FROM visits"
        resp = run_private_query(analyst, account.db.db_name, sql, rho_req=0.05)
        assert resp["status"] == "executed"

        stored = bytes(SysNoiseCache.objects.get(principal=analyst).noise_seed)
        raw_seed = _seed(parse(sql).ast_hash(), analyst.id)
        assert stored != raw_seed
        assert stored == seed_commitment(raw_seed)

        # What an attacker holding the table or a backup could try: replay the noise
        # from the stored value and subtract it. With the seed this recovers the truth
        # exactly; with the commitment it does not.
        answer = resp["result"][0]["value"]
        true_bounded_count = 5 + 5 * 2  # C=5 per patient: patient 0 has 7 visits -> 5; five others have 2
        noise_from_raw = mechanisms.noisy_laplace(0.0, 5, 0.05, KeyedRandom(raw_seed))
        assert answer - noise_from_raw == true_bounded_count  # what storing the seed would allow
        noise_from_stored = mechanisms.noisy_laplace(0.0, 5, 0.05, KeyedRandom(stored))
        assert answer - noise_from_stored != true_bounded_count

    def test_commitment_is_one_way_and_input_dependent(self):
        a, b = seed_commitment(b"x" * 32), seed_commitment(b"y" * 32)
        assert a != b and len(a) == 32
        assert seed_commitment(b"x" * 32) == a
