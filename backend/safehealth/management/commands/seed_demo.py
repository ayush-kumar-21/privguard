"""
Seed the SafeHealth demonstration dataset + catalog, privacy policies, roles,
principals, and budget accounts.

    python manage.py seed_demo               # seed if empty
    python manage.py seed_demo --patients 300
    python manage.py seed_demo --flush       # wipe demo data first, then reseed

Idempotent: catalog/roles/principals use get_or_create; bulk application data is
only generated when the visits table is empty (or after --flush). TRUNCATE is
used for --flush because it does not fire the ledger's append-only row trigger.
"""
from __future__ import annotations

import os
import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from accounts.models import ColumnGrant, Principal, PrincipalRole, Role
from budget.models import BudgetAccount
from catalog.models import (
    SysColumn, SysColumnPrivacy, SysDatabase, SysTable, SysTablePrivacy,
)
from safehealth.models import (
    Department, Diagnosis, Hospital, LabResult, Patient, Region, Staff, Visit,
)

# The demo password is public (it is in the repository), so it is only used with DEBUG on. With DEBUG
# off the passwords must come from the environment, and the command refuses to run without them:
#   PRIVGUARD_DEMO_PASSWORD  alice, frank, erin, carol   PRIVGUARD_ADMIN_PASSWORD  dave (the dba)
DEMO_PASSWORD = "privguard123"
MIN_PASSWORD_LEN = 12


def password_for(username: str, role_name: str) -> str:
    """The password a seeded account gets: env-provided in production, the public demo one in DEBUG."""
    from django.conf import settings
    var = "PRIVGUARD_ADMIN_PASSWORD" if role_name == "dba" else "PRIVGUARD_DEMO_PASSWORD"
    value = os.environ.get(var, "")
    if settings.DEBUG:
        return value or DEMO_PASSWORD
    if len(value) < MIN_PASSWORD_LEN or value == DEMO_PASSWORD:
        raise CommandError(f"{var} must be set to at least {MIN_PASSWORD_LEN} characters (and not the "
                           "public demo password) to seed accounts when DEBUG is off")
    return value
ADMISSION_TYPES = ["EMERGENCY", "ELECTIVE", "URGENT"]
ICD10 = ["I21.9", "E11.9", "J18.9", "N39.0", "C50.9"]
ICD10_RARE = "Z99.9"          # the "outlier patient" signal for the attack demo
LAB_TESTS = [("GLU", "mg/dL", 70, 200), ("HGB", "g/dL", 8, 18), ("CRP", "mg/L", 0, 50)]

# principal_name -> (role, rho_total)  (None rho = no budget account)
PRINCIPALS = {
    "alice": ("dp_analyst", 5.0),
    "frank": ("dp_analyst", 0.2),     # small budget -> exhaustion demo
    "erin": ("auditor", 5.0),
    "carol": ("clinician", None),
    "dave": ("dba", None),
}

FLUSH_TABLES = [
    "lab_results", "diagnoses", "visits", "patients", "staff", "departments",
    "hospitals", "regions", "sys_noise_cache", "sys_predicate_overlap",
    "sys_query", "sys_query_fingerprint", "sys_budget_ledger", "sys_budget_account",
]


class Command(BaseCommand):
    help = "Seed the SafeHealth demo dataset, catalog, policies, roles and budgets."

    def add_arguments(self, parser):
        parser.add_argument("--patients", type=int, default=150)
        parser.add_argument("--flush", action="store_true", help="TRUNCATE demo data first")
        parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")

    def handle(self, *args, **opts):
        rng = random.Random(opts["seed"])

        # Check every password first. Creating an account and only then failing on its password
        # would leave it with no usable password, and a re-run never fixes an existing account.
        for name, (role_name, _) in PRINCIPALS.items():
            password_for(name, role_name)

        if opts["flush"]:
            with connection.cursor() as cur:
                cur.execute(f"TRUNCATE {', '.join(FLUSH_TABLES)} RESTART IDENTITY CASCADE;")
            self.stdout.write(self.style.WARNING("flushed demo tables"))

        db = self._catalog()
        self._principals(db)

        if Visit.objects.exists():
            self.stdout.write(self.style.WARNING(
                "visits already present — catalog/principals ensured; "
                "use --flush to regenerate application data."))
            return

        self._appdata(db, rng, opts["patients"])
        self._summary()

    # ------------------------------------------------------------------
    @transaction.atomic
    def _catalog(self) -> SysDatabase:
        db, _ = SysDatabase.objects.get_or_create(
            db_name="safehealth",
            defaults=dict(dp_model="bounded", default_rho=1.0, default_delta=1e-6),
        )
        # Describe the visits + patients tables in the catalog.
        t_visits, _ = SysTable.objects.get_or_create(db=db, table_name="visits", defaults=dict(root_page_id=0))
        t_patients, _ = SysTable.objects.get_or_create(db=db, table_name="patients", defaults=dict(root_page_id=0))

        def col(table, name, ordinal, dtype, **kw):
            obj, _ = SysColumn.objects.get_or_create(
                table=table, col_name=name,
                defaults=dict(ordinal=ordinal, data_type=dtype, is_nullable=False, **kw))
            return obj

        c_patient = col(t_visits, "patient_id", 1, "INT", is_privacy_unit=True)
        c_charge = col(t_visits, "total_charge", 2, "DOUBLE")
        col(t_visits, "admission_type", 3, "VARCHAR")
        col(t_visits, "dept_id", 4, "INT")
        col(t_patients, "patient_id", 1, "INT", is_privacy_unit=True)
        col(t_patients, "mrn", 2, "VARCHAR", is_encrypted=True)

        SysTablePrivacy.objects.get_or_create(
            table=t_visits,
            defaults=dict(privacy_unit_col=c_patient, max_rows_per_unit=5, max_groups_per_unit=3))
        SysColumnPrivacy.objects.get_or_create(
            column=c_charge,
            defaults=dict(mechanism="laplace", lower_clamp=0.0, upper_clamp=50000.0,
                          max_contrib_per_unit=5, is_groupable=False))

        # Roles + a couple of demonstrative column grants.
        roles = {name: Role.objects.get_or_create(role_name=name)[0]
                 for name in ("dp_analyst", "clinician", "auditor", "dba")}
        ColumnGrant.objects.get_or_create(
            column=c_charge, role=roles["dp_analyst"],
            defaults=dict(access_mode="dp", rho_multiplier=1.0))
        ColumnGrant.objects.get_or_create(
            column=c_charge, role=roles["auditor"],
            defaults=dict(access_mode="dp", rho_multiplier=0.25))  # auditor: less noise
        return db

    def _principals(self, db: SysDatabase) -> None:
        for name, (role_name, rho) in PRINCIPALS.items():
            p, created = Principal.objects.get_or_create(
                username=name, defaults=dict(kind="human"))
            if created or not p.has_usable_password():
                p.set_password(password_for(name, role_name))
                if role_name == "dba":
                    p.is_staff = p.is_superuser = True
                p.save()
            role = Role.objects.get(role_name=role_name)
            PrincipalRole.objects.get_or_create(principal=p, role=role)
            if rho is not None:
                BudgetAccount.objects.get_or_create(
                    principal=p, db=db,
                    defaults=dict(rho_total=rho, delta_target=1e-6, window_type="lifetime"))
        # Database-wide cap on total rho: bounds what colluding analysts can learn
        # together. Headroom lets a few more accounts be added before it binds.
        if db.rho_global_cap is None:
            total = sum(r for _, r in PRINCIPALS.values() if r is not None)
            db.rho_global_cap = total + 5.0
            db.save(update_fields=["rho_global_cap"])

    def _appdata(self, db, rng: random.Random, n_patients: int) -> None:
        regions = Region.objects.bulk_create([
            Region(region_name=n, state_code=c)
            for n, c in [("North", "TN"), ("South", "KL"), ("West", "MH")]])
        hospitals = Hospital.objects.bulk_create([
            Hospital(region=rng.choice(regions), hospital_name=f"Hospital {i}",
                     bed_count=rng.randint(80, 600)) for i in range(4)])
        depts = Department.objects.bulk_create([
            Department(hospital=rng.choice(hospitals), dept_name=n, specialty=n)
            for n in ("Cardiology", "Oncology", "Neurology", "Pediatrics", "Emergency")])
        staff = Staff.objects.bulk_create([
            Staff(dept=rng.choice(depts), full_name=f"Dr. Staff{i}",
                  staff_role=rng.choice(["physician", "nurse", "resident"]),
                  hire_date=timezone.now().date() - timedelta(days=rng.randint(100, 4000)))
            for i in range(30)])

        patients = Patient.objects.bulk_create([
            Patient(region=rng.choice(regions), mrn=f"MRN{i:06d}",
                    birth_year=rng.randint(1940, 2015),
                    sex=rng.choice(["M", "F"]),
                    zip3=rng.choice(["560", "600", "400", "110"]),
                    insurance_type=rng.choice(["PPO", "HMO", "MEDICARE", "SELF"]),
                    enrolled_at=timezone.now().date() - timedelta(days=rng.randint(1, 3000)))
            for i in range(n_patients)])

        base = timezone.now() - timedelta(days=365)
        visits, outlier = [], patients[0]
        for i in range(n_patients * 4):
            p = patients[i % n_patients]
            admit = base + timedelta(hours=i * 3)
            visits.append(Visit(
                patient=p, dept=rng.choice(depts), attending_staff=rng.choice(staff),
                admit_ts=admit,
                discharge_ts=admit + timedelta(hours=rng.randint(6, 240)),
                total_charge=round(rng.uniform(200, 60000), 2),
                admission_type=rng.choice(ADMISSION_TYPES)))
        Visit.objects.bulk_create(visits, batch_size=1000)

        # Diagnoses + labs (give the outlier patient the rare code for the attack demo).
        db_visits = list(Visit.objects.select_related("patient").all())
        diags, labs = [], []
        for v in db_visits:
            rare = (v.patient_id == outlier.patient_id and rng.random() < 0.5)
            n_d = rng.randint(1, 3)
            for j in range(n_d):
                diags.append(Diagnosis(
                    visit=v,
                    icd10_code=ICD10_RARE if (rare and j == 0) else rng.choice(ICD10),
                    is_primary=(j == 0), severity=rng.randint(1, 5)))
            for _ in range(rng.randint(0, 2)):
                code, unit, lo, hi = rng.choice(LAB_TESTS)
                labs.append(LabResult(visit=v, test_code=code,
                                      value_num=round(rng.uniform(lo, hi), 1), unit=unit,
                                      taken_at=v.admit_ts + timedelta(hours=rng.randint(1, 12))))
        Diagnosis.objects.bulk_create(diags, batch_size=1000)
        LabResult.objects.bulk_create(labs, batch_size=1000)

    def _summary(self) -> None:
        counts = {m.__name__: m.objects.count() for m in
                  (Region, Hospital, Department, Staff, Patient, Visit, Diagnosis, LabResult)}
        self.stdout.write(self.style.SUCCESS("Seeded SafeHealth demo:"))
        for name, c in counts.items():
            self.stdout.write(f"  {name:<12} {c}")
        from django.conf import settings
        shown = f"password: {DEMO_PASSWORD}" if settings.DEBUG else "passwords from the environment"
        self.stdout.write(self.style.SUCCESS(f"Principals: {', '.join(PRINCIPALS)}  ({shown})"))
