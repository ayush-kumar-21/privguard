"""Shared fixtures for the PrivGuard test suite."""
import pytest

from accounts.models import Principal
from budget.models import BudgetAccount
from catalog.models import SysDatabase


@pytest.fixture
def analyst(db):
    """A test analyst user."""
    return Principal.objects.create_user(
        username="test_analyst", password="test1234", kind="human"
    )


@pytest.fixture
def test_db(db):
    """A test database entry."""
    return SysDatabase.objects.create(
        db_name="test_safehealth", default_rho=1.0, default_delta=1e-6
    )


@pytest.fixture
def account(analyst, test_db):
    """A budget account for the test analyst."""
    return BudgetAccount.objects.create(
        principal=analyst, db=test_db,
        rho_total=10.0, delta_target=1e-6,
        window_type="lifetime",
    )
