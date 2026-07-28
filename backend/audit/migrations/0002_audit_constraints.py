from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sysquery",
            name="plan_json",
            field=models.JSONField(null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="sysnoisecache",
            name="cached_result",
            field=models.JSONField(),
        ),
        migrations.AlterField(
            model_name="sysquery",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "pending"),
                    ("executed", "executed"),
                    ("cache_hit", "cache_hit"),
                    ("error", "error"),
                    ("rejected_budget", "rejected_budget"),
                    ("rejected_policy", "rejected_policy"),
                    ("rejected_correlation", "rejected_correlation"),
                    ("suppressed_partition", "suppressed_partition"),
                ],
                max_length=24,
            ),
        ),
        migrations.AddConstraint(
            model_name="sysqueryfingerprint",
            constraint=models.CheckConstraint(check=models.Q(times_seen__gte=1), name="ck_fingerprint_times_seen_pos"),
        ),
        migrations.AddConstraint(
            model_name="sysquery",
            constraint=models.CheckConstraint(check=models.Q(rho_charged__gte=0), name="ck_query_rho_nonneg"),
        ),
        migrations.AddConstraint(
            model_name="syspredicateoverlap",
            constraint=models.CheckConstraint(check=models.Q(jaccard_est__gte=0) & models.Q(jaccard_est__lte=1), name="ck_overlap_jaccard_unit"),
        ),
        migrations.AddConstraint(
            model_name="syspredicateoverlap",
            constraint=models.CheckConstraint(check=models.Q(symmetric_diff_est__gte=0), name="ck_overlap_symdiff_nonneg"),
        ),
        migrations.AddConstraint(
            model_name="syspredicateoverlap",
            constraint=models.CheckConstraint(check=models.Q(fp_a__lt=models.F("fp_b")), name="ck_overlap_canonical_order"),
        ),
    ]
