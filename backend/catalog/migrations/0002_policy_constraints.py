from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='syscolumnprivacy',
            constraint=models.CheckConstraint(
                check=(
                    models.Q(lower_clamp__isnull=True, upper_clamp__isnull=True) |
                    (models.Q(lower_clamp__isnull=False, upper_clamp__isnull=False) & models.Q(lower_clamp__lte=models.F('upper_clamp')))
                ),
                name='ck_colpriv_clamp_pair_ordered'
            ),
        ),
        migrations.AddConstraint(
            model_name='syscolumnprivacy',
            constraint=models.CheckConstraint(check=models.Q(max_contrib_per_unit__gte=1), name='ck_colpriv_contrib_pos'),
        ),
        migrations.AddConstraint(
            model_name='syscolumnprivacy',
            constraint=models.CheckConstraint(check=models.Q(min_partition_size__gte=0), name='ck_colpriv_min_partition_nonneg'),
        ),
        migrations.AddConstraint(
            model_name='systableprivacy',
            constraint=models.CheckConstraint(check=models.Q(max_rows_per_unit__gte=1), name='ck_tabpriv_max_rows_pos'),
        ),
        migrations.AddConstraint(
            model_name='systableprivacy',
            constraint=models.CheckConstraint(check=models.Q(max_groups_per_unit__gte=1), name='ck_tabpriv_max_groups_pos'),
        ),
        migrations.AddConstraint(
            model_name='systableprivacy',
            constraint=models.CheckConstraint(check=models.Q(min_partition_rho__gt=0), name='ck_tabpriv_min_rho_pos'),
        ),
        migrations.AddConstraint(
            model_name='syscolumn',
            constraint=models.CheckConstraint(check=models.Q(ordinal__gte=1), name='ck_column_ordinal_pos'),
        ),
    ]
