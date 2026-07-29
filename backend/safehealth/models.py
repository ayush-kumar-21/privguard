"""
SafeHealth Application Data (Subsystem F).

The privacy unit is `Patient.patient_id`: neighbouring databases differ by
exactly one patient. Fact tables (Visit, Diagnosis, LabResult) reach the privacy
unit transitively through Visit.

Derived attributes (`age`, `length_of_stay_days`) are computed properties, NOT
stored columns — this is the 3NF choice argued in the ER documentation (storing
`age` alongside `birth_year` would be an update anomaly).
"""
from datetime import date

from django.db import models


class Region(models.Model):
    region_id = models.AutoField(primary_key=True)
    region_name = models.CharField(max_length=64)
    state_code = models.CharField(max_length=4)

    class Meta:
        db_table = "regions"
        constraints = [
            models.UniqueConstraint(fields=["region_name"], name="uq_region_name"),
        ]

    def __str__(self):
        return self.region_name


class Hospital(models.Model):
    hospital_id = models.AutoField(primary_key=True)
    region = models.ForeignKey(
        Region, on_delete=models.PROTECT,
        db_column="region_id", related_name="hospitals",
    )
    hospital_name = models.CharField(max_length=128)
    bed_count = models.IntegerField()

    class Meta:
        db_table = "hospitals"
        constraints = [
            models.CheckConstraint(check=models.Q(bed_count__gt=0), name="ck_hospital_bed_count_pos"),
        ]

    def __str__(self):
        return self.hospital_name


class Department(models.Model):
    dept_id = models.AutoField(primary_key=True)
    hospital = models.ForeignKey(
        Hospital, on_delete=models.CASCADE,
        db_column="hospital_id", related_name="departments",
    )
    dept_name = models.CharField(max_length=64)
    specialty = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        db_table = "departments"
        constraints = [
            models.UniqueConstraint(fields=["hospital", "dept_name"], name="uq_dept_per_hospital"),
        ]

    def __str__(self):
        return self.dept_name


class Staff(models.Model):
    staff_id = models.AutoField(primary_key=True)
    dept = models.ForeignKey(
        Department, on_delete=models.CASCADE,
        db_column="dept_id", related_name="staff",
    )
    full_name = models.CharField(max_length=128)
    staff_role = models.CharField(max_length=32)
    hire_date = models.DateField()

    class Meta:
        db_table = "staff"
        verbose_name_plural = "staff"

    def __str__(self):
        return self.full_name


class Patient(models.Model):
    """PRIVACY UNIT TABLE. `mrn` is treated as an encrypted / access-controlled column."""
    patient_id = models.AutoField(primary_key=True)
    region = models.ForeignKey(
        Region, on_delete=models.PROTECT,
        db_column="region_id", related_name="patients",
    )
    mrn = models.CharField(max_length=32, help_text="access_mode='denied' for analysts")
    birth_year = models.IntegerField()
    sex = models.CharField(max_length=8)
    zip3 = models.CharField(max_length=3)
    insurance_type = models.CharField(max_length=24)
    enrolled_at = models.DateField()

    class Meta:
        db_table = "patients"
        indexes = [
            # PatientViewSet.search_fields
            models.Index(fields=["zip3"], name="idx_patients_zip3"),
            # PatientViewSet.filterset_fields
            models.Index(fields=["insurance_type"], name="idx_patients_insurance"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(birth_year__gte=1900) & models.Q(birth_year__lte=2100),
                name="ck_patient_birth_year_range",
            ),
            models.UniqueConstraint(fields=["mrn"], name="uq_patient_mrn"),
        ]

    @property
    def age(self) -> int:
        """Derived: CURRENT_YEAR - birth_year."""
        return date.today().year - self.birth_year


class Visit(models.Model):
    visit_id = models.BigAutoField(primary_key=True)
    patient = models.ForeignKey(
        Patient, on_delete=models.PROTECT,
        db_column="patient_id", related_name="visits",
    )
    dept = models.ForeignKey(
        Department, on_delete=models.PROTECT,
        db_column="dept_id", related_name="visits",
    )
    attending_staff = models.ForeignKey(
        Staff, on_delete=models.PROTECT,
        db_column="attending_staff_id", related_name="visits",
    )
    admit_ts = models.DateTimeField()
    discharge_ts = models.DateTimeField(null=True, blank=True)
    total_charge = models.FloatField()
    admission_type = models.CharField(max_length=16)

    class Meta:
        db_table = "visits"
        indexes = [
            models.Index(fields=["admit_ts"], name="idx_visits_admit"),
            models.Index(fields=["patient"], name="idx_visits_patient"),
            # PARTITION BY … ORDER BY for WindowAgg
            models.Index(fields=["patient", "visit_id"], name="idx_visits_patient_vid"),
            # GROUPABLE set in privacy/sql.py
            models.Index(fields=["admission_type"], name="idx_visits_admission_type"),
            # GROUPABLE + FILTERABLE
            models.Index(fields=["dept", "admit_ts"], name="idx_visits_dept_admit"),
            # FILTERABLE + ordering_fields
            models.Index(fields=["total_charge"], name="idx_visits_charge"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(discharge_ts__isnull=True) | models.Q(discharge_ts__gte=models.F("admit_ts")),
                name="ck_visit_discharge_after_admit",
            ),
            models.CheckConstraint(check=models.Q(total_charge__gte=0), name="ck_visit_charge_nonneg"),
        ]

    @property
    def length_of_stay_days(self):
        """Derived from admit/discharge timestamps."""
        if self.discharge_ts and self.admit_ts:
            return (self.discharge_ts - self.admit_ts).days
        return None


class Diagnosis(models.Model):
    diagnosis_id = models.BigAutoField(primary_key=True)
    visit = models.ForeignKey(
        Visit, on_delete=models.CASCADE,
        db_column="visit_id", related_name="diagnoses",
    )
    icd10_code = models.CharField(max_length=10)
    is_primary = models.BooleanField(default=False)
    severity = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "diagnoses"
        verbose_name_plural = "diagnoses"
        indexes = [
            # DiagnosisViewSet.filterset_fields + Attack Lab
            models.Index(fields=["icd10_code"], name="idx_diag_icd10"),
            # DiagnosisViewSet.filterset_fields
            models.Index(fields=["visit", "is_primary"], name="idx_diag_visit_primary"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(severity__isnull=True) | (models.Q(severity__gte=1) & models.Q(severity__lte=5)),
                name="ck_diagnosis_severity_range",
            ),
        ]


class LabResult(models.Model):
    lab_id = models.BigAutoField(primary_key=True)
    visit = models.ForeignKey(
        Visit, on_delete=models.CASCADE,
        db_column="visit_id", related_name="lab_results",
    )
    test_code = models.CharField(max_length=16)
    value_num = models.FloatField(null=True, blank=True)
    unit = models.CharField(max_length=16, null=True, blank=True)
    taken_at = models.DateTimeField()

    class Meta:
        db_table = "lab_results"
        indexes = [
            # LabResultViewSet.filterset_fields
            models.Index(fields=["visit", "test_code"], name="idx_lab_visit_test"),
            models.Index(fields=["taken_at"], name="idx_lab_taken_at"),
        ]
