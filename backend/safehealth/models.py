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
