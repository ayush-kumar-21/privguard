"""Regression tests for the DP hardening work.

Each class pins one published attack class so it cannot silently return:

* TestOutputChannels   - the audit API must not expose exact counts or timing
                         (budget-bypass / timing channel; Haeberlen et al.)
* TestExactSamplers    - integer-only noise with correct distribution, and
                         conservative parameter rounding (Mironov 2012; Jin et
                         al. S&P 2022; Canonne-Kamath-Steinke 2020)
* TestPartitionSelection - L0-aware eps/delta (Wilson et al., PoPETs 2020)
* TestGlobalBudgetCap  - colluding analysts cannot exceed a database-wide cap
"""
import math
from fractions import Fraction

import pytest
from django.db import connection
from rest_framework.test import APIClient

from accounts.models import Principal, PrincipalRole, Role
from audit.models import SysQuery
from budget.models import BudgetAccount
from privacy import mechanisms
from privacy.mechanisms import KeyedRandom
from privacy.service import Policy, _true_aggregate, partition_params
from privacy.sql import parse

PASSWORD = "S3cure-pass-123"


def _client_for(user):
    client = APIClient()
    resp = client.post(
        "/api/auth/login/", {"username": user.username, "password": PASSWORD}, format="json"
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return client


def _user(name, role=None):
    user = Principal.objects.create_user(username=name, password=PASSWORD)
    if role:
        r, _ = Role.objects.get_or_create(role_name=role)
        PrincipalRole.objects.create(principal=user, role=r)
    return user


def _executed_query(account, latency_ms=12.5):
    return SysQuery.objects.create(
        principal=account.principal, db=account.db, sql_text="SELECT COUNT(*) FROM visits",
        status="executed", rho_charged=0.05, latency_ms=latency_ms,
    )


# --------------------------------------------------------------------------
@pytest.mark.django_db
class TestOutputChannels:
    def test_no_exact_count_is_persisted_or_returned(self, analyst, account):
        # The column that once held the true count is gone from the model, the
        # database (and therefore backups) and the audit view, not just the API.
        from django.core.exceptions import FieldDoesNotExist

        with pytest.raises(FieldDoesNotExist):
            SysQuery._meta.get_field("rows_scanned")
        with connection.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name IN ('sys_query', 'v_query_audit') AND column_name = 'rows_scanned'"
            )
            assert cur.fetchall() == []
        _executed_query(account)
        analyst.set_password(PASSWORD)
        analyst.save()
        body = _client_for(analyst).get("/api/queries/").data["results"][0]
        assert "rows_scanned" not in body

    def test_analyst_does_not_see_latency(self, analyst, account):
        _executed_query(account)
        analyst.set_password(PASSWORD)
        analyst.save()
        body = _client_for(analyst).get("/api/queries/").data["results"][0]
        assert "latency_ms" not in body

    def test_analyst_cannot_sort_by_hidden_latency(self, analyst, account):
        # Sorting by a hidden column would leak its order.
        for ms in (5.0, 90.0, 20.0):
            _executed_query(account, latency_ms=ms)
        analyst.set_password(PASSWORD)
        analyst.save()
        client = _client_for(analyst)
        by_latency = [q["query_id"] for q in client.get("/api/queries/?ordering=latency_ms").data["results"]]
        default = [q["query_id"] for q in client.get("/api/queries/").data["results"]]
        assert by_latency == default  # the ordering parameter was ignored

    def test_dba_sees_latency(self, account):
        _executed_query(account, latency_ms=12.5)
        dba = _user("dba_user", role="dba")
        body = _client_for(dba).get("/api/queries/").data["results"][0]
        assert body["latency_ms"] == 12.5


# --------------------------------------------------------------------------
class TestExactSamplers:
    N = 20000

    def test_discrete_laplace_matches_theory(self):
        # eps = s/t = 1/2: P(Z=z) = (1-e^-eps)/(1+e^-eps) * e^(-eps|z|)
        rng = KeyedRandom(b"laplace-test")
        samples = [mechanisms.sample_discrete_laplace(1, 2, rng) for _ in range(self.N)]
        eps = 0.5
        p0 = (1 - math.exp(-eps)) / (1 + math.exp(-eps))
        assert abs(samples.count(0) / self.N - p0) < 0.015
        p1 = p0 * math.exp(-eps)
        assert abs(samples.count(1) / self.N - p1) < 0.015
        assert abs(sum(samples) / self.N) < 0.1  # symmetric

    def test_discrete_gaussian_variance(self):
        rng = KeyedRandom(b"gauss-test")
        sigma_sq = Fraction(4)
        samples = [mechanisms.sample_discrete_gaussian(sigma_sq, rng) for _ in range(self.N)]
        mean = sum(samples) / self.N
        var = sum((x - mean) ** 2 for x in samples) / self.N
        assert abs(mean) < 0.1
        assert abs(var - 4.0) < 0.3

    def test_bernoulli_exp(self):
        rng = KeyedRandom(b"bern-test")
        gamma = Fraction(3, 2)
        hits = sum(mechanisms.bernoulli_exp(gamma, rng) for _ in range(self.N))
        assert abs(hits / self.N - math.exp(-1.5)) < 0.015

    def test_same_seed_same_noise(self):
        a = mechanisms.noisy_laplace(100.0, 5.0, 0.05, KeyedRandom(b"s"))
        b = mechanisms.noisy_laplace(100.0, 5.0, 0.05, KeyedRandom(b"s"))
        c = mechanisms.noisy_laplace(100.0, 5.0, 0.05, KeyedRandom(b"other"))
        assert a == b
        assert a != c  # (overwhelmingly likely; seeds are fixed so this is stable)

    def test_outputs_lie_on_the_grid(self):
        # No sub-grid low-order bits that depend on the input (Mironov's channel).
        rng = KeyedRandom(b"grid")
        for true in (0.1, 12345.678, 1e6 + 0.3):
            out = mechanisms.noisy_laplace(true, 50.0, 0.05, rng, grid=mechanisms.SUM_GRID)
            cells = out / mechanisms.SUM_GRID
            assert cells == int(cells)

    @pytest.mark.parametrize("sensitivity", [1.0, 5.0, 250_000.0, 1e9])
    @pytest.mark.parametrize("rho", [1e-4, 0.05, 1.0])
    def test_epsilon_never_exceeds_requested(self, sensitivity, rho):
        # Rounding must always ADD noise: eps' per grid cell <= eps0 / grid-sensitivity.
        grid = mechanisms.SUM_GRID
        eps = mechanisms.discrete_laplace_epsilon(sensitivity, rho, grid)
        bound = math.sqrt(2 * rho) / mechanisms.grid_sensitivity(sensitivity, grid)
        assert float(eps) <= bound * (1 + 1e-12)
        assert eps > 0

    def test_gaussian_rho_never_understated(self):
        # sigma^2 used must be >= the real-valued sigma^2 for the requested rho.
        rho, sens = 0.05, 3.0
        rng = KeyedRandom(b"x")
        # reproduce the parameter computation
        delta_g = mechanisms.grid_sensitivity(sens, 1.0)
        needed = delta_g**2 / (2 * rho)
        rho_q = mechanisms._round_down_fraction(rho)
        used = Fraction(delta_g**2) / (2 * rho_q)
        assert float(used) >= needed * (1 - 1e-12)
        mechanisms.noisy_gaussian(10.0, sens, rho, rng)  # runs without error


# --------------------------------------------------------------------------
class TestPartitionSelection:
    def test_noise_grows_with_l0_bound(self):
        e1, t1 = partition_params(0.01, 1, 1e-2)
        e3, t3 = partition_params(0.01, 3, 1e-2)
        # eps0 shrinks like 1/sqrt(G), so the threshold must rise.
        assert float(e3) < float(e1)
        assert float(e3) == pytest.approx(float(e1) / math.sqrt(3), rel=1e-3)
        assert t3 > t1

    @pytest.mark.parametrize("groups", [1, 3, 5])
    def test_composed_cost_within_budget(self, groups):
        rho_part = 0.01
        eps, _ = partition_params(rho_part, groups, 1e-2)
        # G releases, each eps0-DP => G * eps0^2 / 2 zCDP; must not exceed rho_part.
        assert groups * float(eps) ** 2 / 2 <= rho_part * (1 + 1e-9)

    def test_lone_unit_release_probability_within_delta(self):
        # A partition containing exactly one unit must be released with total
        # probability <= delta, even though the unit sits in G partitions.
        groups, delta = 3, 0.1
        eps, tau = partition_params(0.01, groups, delta)
        rng = KeyedRandom(b"partition-test")
        trials, released = 20000, 0
        for _ in range(trials):
            if any(
                1 + mechanisms.sample_discrete_laplace(eps.numerator, eps.denominator, rng) > tau
                for _ in range(groups)
            ):
                released += 1
        assert released / trials <= delta * 1.12


# --------------------------------------------------------------------------
def _reserve(account, rho):
    q = SysQuery.objects.create(
        principal=account.principal, db=account.db,
        sql_text="SELECT COUNT(*)", status="pending", rho_charged=0,
    )
    with connection.cursor() as cur:
        cur.execute(
            "CALL sp_reserve_budget(%s,%s,%s,%s,%s,%s,%s,%s)",
            [account.account_id, q.query_id, rho, "laplace", 1.0, 1.0, 0.1, None],
        )
        return cur.fetchone()[-1]


@pytest.mark.django_db
class TestGlobalBudgetCap:
    @pytest.fixture
    def colluders(self, test_db):
        test_db.rho_global_cap = 1.0
        test_db.save()
        accounts = []
        for name in ("mallory_a", "mallory_b"):
            user = _user(name)
            accounts.append(BudgetAccount.objects.create(
                principal=user, db=test_db, rho_total=5.0, delta_target=1e-6))
        return accounts

    def test_cap_blocks_second_analyst(self, colluders):
        a, b = colluders
        assert _reserve(a, 0.6) == "reserved"
        # b has plenty of personal budget, but the database-wide cap is 1.0.
        assert _reserve(b, 0.6) == "rejected_global_cap"
        b.refresh_from_db()
        assert b.rho_reserved == 0

    def test_cap_allows_spend_up_to_the_limit(self, colluders):
        a, b = colluders
        assert _reserve(a, 0.5) == "reserved"
        assert _reserve(b, 0.5) == "reserved"
        assert _reserve(a, 0.01) == "rejected_global_cap"

    def test_no_cap_means_unlimited(self, colluders, test_db):
        test_db.rho_global_cap = None
        test_db.save()
        a, b = colluders
        assert _reserve(a, 2.0) == "reserved"
        assert _reserve(b, 2.0) == "reserved"

    def test_per_account_limit_still_applies_first(self, colluders):
        a, _ = colluders
        assert _reserve(a, 6.0) == "rejected_budget"


# --------------------------------------------------------------------------
@pytest.mark.django_db
class TestGroupedAggregate:
    """Regression: GROUP BY used to return one group per *row* (n=1, units=1)."""

    def _rows(self, sql):
        return _true_aggregate(parse(sql), Policy(5, 3, "laplace", 0.0, 1000.0))

    def test_one_group_per_value_not_per_row(self, visits):
        rows = self._rows("SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type")
        assert {r["admission_type"] for r in rows} == {"ELECTIVE", "URGENT"}
        assert len(rows) == 2

    def test_units_count_distinct_patients(self, visits):
        rows = {r["admission_type"]: r for r in
                self._rows("SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type")}
        assert rows["ELECTIVE"]["units"] == 6 and rows["URGENT"]["units"] == 5

    def test_contribution_bound_is_applied(self, visits):
        # The L-inf bound C=5 applies per (unit, group): patient 0's 7 ELECTIVE visits
        # are cut to 5, so ELECTIVE has 5+5 rows and URGENT 5, not the raw 12 and 5.
        by_type = {r["admission_type"]: r["n"] for r in self._rows(
            "SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type")}
        assert by_type == {"ELECTIVE": 10, "URGENT": 5}

    def test_scalar_aggregate_still_bounded(self, visits):
        agg = self._rows("SELECT COUNT(*) FROM visits")
        assert agg["n"] <= 5 * 6


# --------------------------------------------------------------------------
class TestSecretGuard:
    """Production must not run with secrets that are public in the repository."""

    def test_placeholders_rejected_in_production(self):
        from django.core.exceptions import ImproperlyConfigured

        from privguard.security import KNOWN_PLACEHOLDERS, check_secrets

        for bad in sorted(KNOWN_PLACEHOLDERS):
            with pytest.raises(ImproperlyConfigured):
                check_secrets(False, SECRET_KEY="real-" + "x" * 40, PRIVGUARD_SERVER_SECRET=bad)
            with pytest.raises(ImproperlyConfigured):
                check_secrets(False, SECRET_KEY=bad, PRIVGUARD_SERVER_SECRET="real-" + "x" * 40)

    def test_names_the_offending_setting(self):
        from django.core.exceptions import ImproperlyConfigured

        from privguard.security import check_secrets

        with pytest.raises(ImproperlyConfigured, match="PRIVGUARD_SERVER_SECRET"):
            check_secrets(False, SECRET_KEY="k" * 40, PRIVGUARD_SERVER_SECRET="dev-noise-secret")

    def test_real_secrets_and_debug_mode_pass(self):
        from privguard.security import check_secrets

        check_secrets(False, SECRET_KEY="k" * 40, PRIVGUARD_SERVER_SECRET="n" * 40)
        check_secrets(True, SECRET_KEY="dev-insecure-change-me", PRIVGUARD_SERVER_SECRET="dev-noise-secret")

    def test_every_secret_the_repo_ships_is_a_known_placeholder(self):
        # If someone adds a new committed placeholder, it must be added to the guard.
        import re
        from pathlib import Path

        from privguard.security import KNOWN_PLACEHOLDERS

        example = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
        for key in ("SECRET_KEY", "PRIVGUARD_SERVER_SECRET"):
            value = re.search(rf"^{key}=(.*)$", example, re.M).group(1).strip()
            assert value in KNOWN_PLACEHOLDERS, f"{key} in .env.example is not guarded: {value!r}"
