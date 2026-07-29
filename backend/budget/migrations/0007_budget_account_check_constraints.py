"""
BudgetAccount CHECK constraints.

These were declared in BudgetAccount.Meta.constraints from the start but never
had a migration, so they existed only in the model layer — the database was
enforcing none of them. ck_account_never_oversold is the important one: it is
the table-level backstop for the two-phase RESERVE/COMMIT protocol, ensuring
rho_spent + rho_reserved can never exceed rho_total even if the stored
procedures are bypassed.
"""
import django.db.models.expressions
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budget', '0006_ledger_indexes'),
        ('catalog', '0002_policy_constraints'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='budgetaccount',
            constraint=models.CheckConstraint(condition=models.Q(('rho_total__gt', 0)), name='ck_account_rho_total_pos'),
        ),
        migrations.AddConstraint(
            model_name='budgetaccount',
            constraint=models.CheckConstraint(condition=models.Q(('rho_spent__gte', 0)), name='ck_account_rho_spent_nonneg'),
        ),
        migrations.AddConstraint(
            model_name='budgetaccount',
            constraint=models.CheckConstraint(condition=models.Q(('rho_reserved__gte', 0)), name='ck_account_rho_reserved_nonneg'),
        ),
        migrations.AddConstraint(
            model_name='budgetaccount',
            constraint=models.CheckConstraint(condition=models.Q(('rho_spent__lte', django.db.models.expressions.CombinedExpression(models.F('rho_total'), '-', models.F('rho_reserved')))), name='ck_account_never_oversold'),
        ),
        migrations.AddConstraint(
            model_name='budgetaccount',
            constraint=models.CheckConstraint(condition=models.Q(('delta_target__gt', 0), ('delta_target__lt', 1)), name='ck_account_delta_in_unit_interval'),
        ),
        migrations.AddConstraint(
            model_name='budgetaccount',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('window_type', 'refill'), _negated=True), ('refill_period_s__gt', 0), _connector='OR'), name='ck_account_refill_needs_period'),
        ),
    ]
