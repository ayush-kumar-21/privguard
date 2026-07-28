"""Performance indexes justified by actual query patterns in privacy/sql.py and api/views.py."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("safehealth", "0002_app_constraints"),
    ]

    operations = [
        # visits (patient_id, visit_id): PARTITION BY … ORDER BY for WindowAgg
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["patient", "visit_id"], name="idx_visits_patient_vid"),
        ),
        # visits (admission_type): GROUPABLE set in privacy/sql.py
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["admission_type"], name="idx_visits_admission_type"),
        ),
        # visits (dept_id, admit_ts): GROUPABLE + FILTERABLE
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["dept", "admit_ts"], name="idx_visits_dept_admit"),
        ),
        # visits (total_charge): FILTERABLE + ordering_fields
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["total_charge"], name="idx_visits_charge"),
        ),
        # patients (zip3): PatientViewSet.search_fields
        migrations.AddIndex(
            model_name="patient",
            index=models.Index(fields=["zip3"], name="idx_patients_zip3"),
        ),
        # patients (insurance_type): PatientViewSet.filterset_fields
        migrations.AddIndex(
            model_name="patient",
            index=models.Index(fields=["insurance_type"], name="idx_patients_insurance"),
        ),
        # diagnoses (icd10_code): DiagnosisViewSet.filterset_fields + Attack Lab
        migrations.AddIndex(
            model_name="diagnosis",
            index=models.Index(fields=["icd10_code"], name="idx_diag_icd10"),
        ),
        # diagnoses (visit_id, is_primary): DiagnosisViewSet.filterset_fields
        migrations.AddIndex(
            model_name="diagnosis",
            index=models.Index(fields=["visit", "is_primary"], name="idx_diag_visit_primary"),
        ),
        # lab_results (visit_id, test_code): LabResultViewSet.filterset_fields
        migrations.AddIndex(
            model_name="labresult",
            index=models.Index(fields=["visit", "test_code"], name="idx_lab_visit_test"),
        ),
        # lab_results (taken_at): LabResultViewSet.filterset_fields
        migrations.AddIndex(
            model_name="labresult",
            index=models.Index(fields=["taken_at"], name="idx_lab_taken_at"),
        ),
    ]
