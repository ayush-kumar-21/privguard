"""Audit and budget ledger performance indexes."""
from django.db import migrations, models


PARTIAL_INDEX_SQL = """
CREATE INDEX idx_query_pending ON sys_query (submitted_at)
    WHERE status = 'pending';
"""
PARTIAL_INDEX_DROP = "DROP INDEX IF EXISTS idx_query_pending;"


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0002_audit_constraints"),
        ("budget", "0002_ledger_integrity_and_views"),
    ]

    operations = [
        # sys_query (status, submitted_at DESC): QueryAuditViewSet filter+order
        migrations.AddIndex(
            model_name="sysquery",
            index=models.Index(fields=["status", "-submitted_at"], name="idx_query_status_submitted"),
        ),
        # sys_query (submitted_at) WHERE status='pending': reaper's predicate
        migrations.RunSQL(PARTIAL_INDEX_SQL, reverse_sql=PARTIAL_INDEX_DROP),
        # sys_query_fingerprint (table_id, agg_kind): fingerprint analytics
        migrations.AddIndex(
            model_name="sysqueryfingerprint",
            index=models.Index(fields=["table", "agg_kind"], name="idx_fp_table_agg"),
        ),
    ]
