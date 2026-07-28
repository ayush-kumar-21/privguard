from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("safehealth", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='patient',
            constraint=models.CheckConstraint(check=models.Q(birth_year__gte=1900) & models.Q(birth_year__lte=2100), name='ck_patient_birth_year_range'),
        ),
        migrations.AddConstraint(
            model_name='patient',
            constraint=models.UniqueConstraint(fields=('mrn',), name='uq_patient_mrn'),
        ),
        migrations.AddConstraint(
            model_name='visit',
            constraint=models.CheckConstraint(check=models.Q(discharge_ts__isnull=True) | models.Q(discharge_ts__gte=models.F('admit_ts')), name='ck_visit_discharge_after_admit'),
        ),
        migrations.AddConstraint(
            model_name='visit',
            constraint=models.CheckConstraint(check=models.Q(total_charge__gte=0), name='ck_visit_charge_nonneg'),
        ),
        migrations.AddConstraint(
            model_name='diagnosis',
            constraint=models.CheckConstraint(check=models.Q(severity__isnull=True) | (models.Q(severity__gte=1) & models.Q(severity__lte=5)), name='ck_diagnosis_severity_range'),
        ),
        migrations.AddConstraint(
            model_name='hospital',
            constraint=models.CheckConstraint(check=models.Q(bed_count__gt=0), name='ck_hospital_bed_count_pos'),
        ),
        migrations.AddConstraint(
            model_name='region',
            constraint=models.UniqueConstraint(fields=('region_name',), name='uq_region_name'),
        ),
        migrations.AddConstraint(
            model_name='department',
            constraint=models.UniqueConstraint(fields=('hospital', 'dept_name'), name='uq_dept_per_hospital'),
        ),
    ]
