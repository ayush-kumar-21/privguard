from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("budget", "0008_global_budget_cap")]

    operations = [
        migrations.AddField(
            model_name="budgetaccount",
            name="max_queries",
            field=models.PositiveIntegerField(
                blank=True, null=True, help_text="runs allowed; empty = unlimited"
            ),
        ),
        migrations.AddField(
            model_name="budgetaccount",
            name="queries_used",
            field=models.PositiveIntegerField(default=0, help_text="runs used so far (counted even when unlimited)"),
        ),
    ]
