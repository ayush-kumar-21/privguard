"""
Stop persisting the exact (un-noised) row count.

sys_query.rows_scanned held the true bounded count of every query. The API no
longer returns it, but the column was also read by the v_query_audit view and
copied into every backup, so anyone with database or dump access could read the
exact answer to every query. Nothing needs it, so the column is removed and the
view rebuilt without it.
"""
from django.db import migrations

VIEW_OLD = """
CREATE OR REPLACE VIEW v_query_audit AS
SELECT
    q.query_id,
    p.username     AS principal_name,
    d.db_name,
    q.status,
    q.rho_charged,
    q.rows_scanned,
    q.latency_ms,
    q.submitted_at,
    f.agg_kind,
    t.table_name   AS target_table,
    left(q.sql_text, 200) AS sql_preview
FROM sys_query q
JOIN sys_principal p ON p.id = q.principal_id
JOIN sys_database  d ON d.db_id = q.db_id
LEFT JOIN sys_query_fingerprint f ON f.fingerprint_id = q.fingerprint_id
LEFT JOIN sys_table t ON t.table_id = f.table_id;
"""

VIEW_NEW = """
CREATE OR REPLACE VIEW v_query_audit AS
SELECT
    q.query_id,
    p.username     AS principal_name,
    d.db_name,
    q.status,
    q.rho_charged,
    q.delta_charged,
    q.latency_ms,
    q.submitted_at,
    f.agg_kind,
    t.table_name   AS target_table,
    left(q.sql_text, 200) AS sql_preview
FROM sys_query q
JOIN sys_principal p ON p.id = q.principal_id
JOIN sys_database  d ON d.db_id = q.db_id
LEFT JOIN sys_query_fingerprint f ON f.fingerprint_id = q.fingerprint_id
LEFT JOIN sys_table t ON t.table_id = f.table_id;
"""

DROP = "DROP VIEW IF EXISTS v_query_audit;"


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0004_dp_hardening"),
        ("budget", "0008_global_budget_cap"),  # defines v_query_audit (0002) and keeps it alive
    ]

    operations = [
        migrations.RunSQL(DROP, reverse_sql=VIEW_OLD),
        migrations.RemoveField(model_name="sysquery", name="rows_scanned"),
        migrations.RunSQL(VIEW_NEW, reverse_sql=DROP),
    ]
