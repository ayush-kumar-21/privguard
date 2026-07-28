"""
Ledger reconciliation: fn_reconcile_account + v_ledger_reconciliation.

This makes the ER doc's invariant (rho_spent == Σ COMMIT) executable.
The management command `verify_budgets` reads the view.
"""
from django.db import migrations


RECONCILE_FN = """
CREATE OR REPLACE FUNCTION fn_reconcile_account(p_account_id integer)
RETURNS TABLE(rho_spent_cached double precision,
              rho_spent_ledger double precision,
              rho_reserved_cached double precision,
              rho_reserved_ledger double precision,
              drift double precision,
              chain_ok boolean)
LANGUAGE sql STABLE AS $$
WITH l AS (
    SELECT
        COALESCE(SUM(rho_delta) FILTER (WHERE op = 'COMMIT'), 0)
      + COALESCE(SUM(rho_delta) FILTER (WHERE op = 'REFILL'), 0) AS spent,
        COALESCE(SUM(rho_delta) FILTER (
            WHERE op = 'RESERVE' AND NOT EXISTS (
                SELECT 1 FROM sys_budget_ledger s
                 WHERE s.query_id = sys_budget_ledger.query_id
                   AND s.op IN ('COMMIT', 'ABORT'))), 0) AS reserved
    FROM sys_budget_ledger WHERE account_id = p_account_id)
SELECT a.rho_spent, l.spent, a.rho_reserved, l.reserved,
       abs(a.rho_spent - l.spent) + abs(a.rho_reserved - l.reserved),
       (SELECT ok FROM fn_verify_ledger(p_account_id))
FROM sys_budget_account a, l WHERE a.account_id = p_account_id;
$$;
"""
RECONCILE_FN_DROP = "DROP FUNCTION IF EXISTS fn_reconcile_account(integer);"

RECONCILE_VIEW = """
CREATE OR REPLACE VIEW v_ledger_reconciliation AS
SELECT a.account_id, p.username AS principal_name, d.db_name, r.*
  FROM sys_budget_account a
  JOIN sys_principal p ON p.id = a.principal_id
  JOIN sys_database  d ON d.db_id = a.db_id
 CROSS JOIN LATERAL fn_reconcile_account(a.account_id) r;
"""
RECONCILE_VIEW_DROP = "DROP VIEW IF EXISTS v_ledger_reconciliation;"


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0004_budget_protocol_v2"),
        ("accounts", "0001_initial"),
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(RECONCILE_FN, reverse_sql=RECONCILE_FN_DROP),
        migrations.RunSQL(RECONCILE_VIEW, reverse_sql=RECONCILE_VIEW_DROP),
    ]
