"""
Budget ledger raw-SQL constraints.

These enforce invariants that Django's ORM CheckConstraint cannot express
(octet_length, partial unique indexes, conditional NOT NULL).
"""
from django.db import migrations


CONSTRAINTS_SQL = """
-- Hash columns must be exactly 32 bytes (SHA-256 digest length).
-- Django's BinaryField max_length is ignored for bytea; this is the real guard.
ALTER TABLE sys_budget_ledger
  ADD CONSTRAINT ck_ledger_hash_len
  CHECK (octet_length(prev_hash) = 32 AND octet_length(entry_hash) = 32);

-- Settle-once / reserve-once: partial unique indexes enforce idempotency.
-- A duplicated CALL from a retried HTTP request cannot double-charge.
CREATE UNIQUE INDEX uq_ledger_reserve_once
  ON sys_budget_ledger (query_id) WHERE op = 'RESERVE';
CREATE UNIQUE INDEX uq_ledger_settle_once
  ON sys_budget_ledger (query_id) WHERE op IN ('COMMIT', 'ABORT');

-- REFILL rows have no query; all other ops must reference one.
ALTER TABLE sys_budget_ledger
  ADD CONSTRAINT ck_ledger_query_id_required
  CHECK (op = 'REFILL' OR query_id IS NOT NULL);
"""

CONSTRAINTS_DROP = """
ALTER TABLE sys_budget_ledger DROP CONSTRAINT IF EXISTS ck_ledger_query_id_required;
DROP INDEX IF EXISTS uq_ledger_settle_once;
DROP INDEX IF EXISTS uq_ledger_reserve_once;
ALTER TABLE sys_budget_ledger DROP CONSTRAINT IF EXISTS ck_ledger_hash_len;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0002_ledger_integrity_and_views"),
    ]

    operations = [
        migrations.RunSQL(CONSTRAINTS_SQL, reverse_sql=CONSTRAINTS_DROP),
    ]
