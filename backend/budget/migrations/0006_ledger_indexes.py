"""Budget ledger performance indexes."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("budget", "0005_reconciliation"),
    ]

    operations = [
        # sys_budget_ledger (query_id): fn_reconcile_account's NOT EXISTS
        migrations.AddIndex(
            model_name="budgetledger",
            index=models.Index(fields=["query"], name="idx_ledger_query_id"),
        ),
        # sys_budget_ledger (logged_at): burn-down chart time-range scans
        migrations.AddIndex(
            model_name="budgetledger",
            index=models.Index(fields=["logged_at"], name="idx_ledger_logged_at"),
        ),
    ]
