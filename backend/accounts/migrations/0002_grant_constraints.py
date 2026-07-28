from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='columngrant',
            constraint=models.CheckConstraint(check=models.Q(rho_multiplier__gt=0), name='ck_grant_rho_multiplier_pos'),
        ),
        migrations.AddConstraint(
            model_name='principalattribute',
            constraint=models.CheckConstraint(check=models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=models.F('granted_at')), name='ck_principal_attr_expiry_after_grant'),
        ),
    ]
