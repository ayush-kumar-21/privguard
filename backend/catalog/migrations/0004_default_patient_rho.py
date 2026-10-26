from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_dp_hardening")]

    operations = [
        migrations.AddField(
            model_name="sysdatabase",
            name="default_patient_rho",
            field=models.FloatField(
                default=10.0,
                help_text="zCDP allowance of each PATIENT across all analysts (sys_patient_budget). "
                          "Unlike rho_global_cap it is per privacy unit, so one popular patient "
                          "cannot use up the allowance of everyone else.",
            ),
        ),
        migrations.AddConstraint(
            model_name="sysdatabase",
            constraint=models.CheckConstraint(
                condition=models.Q(default_patient_rho__gt=0), name="ck_database_patient_rho_pos"),
        ),
    ]
