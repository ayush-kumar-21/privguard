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


@pytest.fixture
def visits(db):
    """6 patients, 2 admission types. Patient 0 has 7 ELECTIVE visits (above the C=5 bound);
    the other 5 patients have one ELECTIVE and one URGENT visit each."""
    from datetime import date, datetime, timezone

    from safehealth.models import Department, Hospital, Patient, Region, Staff, Visit

    region = Region.objects.create(region_name="R", state_code="RR")
    hospital = Hospital.objects.create(region=region, hospital_name="H", bed_count=10)
    dept = Department.objects.create(hospital=hospital, dept_name="D", specialty="gen")
    staff = Staff.objects.create(dept=dept, full_name="S", staff_role="md", hire_date=date(2020, 1, 1))
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(6):
        patient = Patient.objects.create(
            region=region, mrn=f"mrn{i}", birth_year=1980, sex="F", zip3="123",
            insurance_type="p", enrolled_at=date(2025, 1, 1))
        for j in range(7 if i == 0 else 2):
            Visit.objects.create(
                patient=patient, dept=dept, attending_staff=staff, admit_ts=when,
                total_charge=100.0, admission_type="ELECTIVE" if (i == 0 or j % 2 == 0) else "URGENT")


