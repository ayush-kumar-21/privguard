"""
Budget protocol v2: hardened procedures + TRUNCATE guard.

Changes to sp_reserve_budget:
  (a) read status in the locking SELECT
  (b) suspended/exhausted account cannot reserve
  (c) query must belong to the account's principal
  (d) idempotency: already_reserved guard

Changes to sp_settle_budget:
  (a) already_settled guard
  (b) passes sensitivity and sigma_or_scale through
  (c) clears 'exhausted' on abort if budget is restored

New: sp_refill_budget — makes the REFILL ledger op real.
New: trg_ledger_no_truncate — statement-level TRUNCATE guard.
"""
from django.db import migrations


RESERVE_V2 = """
CREATE OR REPLACE PROCEDURE sp_reserve_budget(
    IN    p_account_id     integer,
    IN    p_query_id       bigint,
    IN    p_rho_req        double precision,
    IN    p_mechanism      varchar,
    IN    p_sensitivity    double precision,
    IN    p_sigma_or_scale double precision,
    IN    p_eps_equiv      double precision,
    INOUT p_status         varchar
) LANGUAGE plpgsql AS $proc$
DECLARE
    v_total    double precision;
    v_spent    double precision;
    v_reserved double precision;
    v_status   varchar;
    v_owner    integer;
BEGIN
    -- Lock the account row: concurrent reservations serialise here.
    SELECT rho_total, rho_spent, rho_reserved, status, principal_id
      INTO v_total, v_spent, v_reserved, v_status, v_owner
      FROM sys_budget_account
     WHERE account_id = p_account_id
     FOR UPDATE;

    IF NOT FOUND THEN
        p_status := 'no_such_account';
        RETURN;
    END IF;

    -- (b) a suspended or exhausted account cannot reserve
    IF v_status <> 'active' THEN
        p_status := 'account_' || v_status;
        RETURN;
    END IF;

    -- (c) the query must belong to the account's principal
    IF NOT EXISTS (SELECT 1 FROM sys_query q
                    WHERE q.query_id = p_query_id AND q.principal_id = v_owner) THEN
        p_status := 'principal_mismatch';
        RETURN;
    END IF;

    -- (d) idempotency: a retried HTTP request must not reserve twice
    IF EXISTS (SELECT 1 FROM sys_budget_ledger
                WHERE query_id = p_query_id AND op = 'RESERVE') THEN
        p_status := 'already_reserved';
        RETURN;
    END IF;

    IF v_spent + v_reserved + p_rho_req > v_total THEN
        p_status := 'rejected_budget';
        RETURN;
    END IF;

    UPDATE sys_budget_account
       SET rho_reserved = rho_reserved + p_rho_req
     WHERE account_id = p_account_id;

    INSERT INTO sys_budget_ledger(
        account_id, query_id, op, rho_delta, rho_after, eps_equiv,
        mechanism, sensitivity, sigma_or_scale, logged_at,
        prev_hash, entry_hash)
    VALUES (
        p_account_id, p_query_id, 'RESERVE', p_rho_req,
        v_spent + v_reserved + p_rho_req, p_eps_equiv,
        p_mechanism, p_sensitivity, p_sigma_or_scale, now(),
        decode(repeat('00', 32), 'hex'),
        decode(repeat('00', 32), 'hex'));

    p_status := 'reserved';
END;
$proc$;
"""

SETTLE_V2 = """
CREATE OR REPLACE PROCEDURE sp_settle_budget(
    IN    p_account_id     integer,
    IN    p_query_id       bigint,
    IN    p_rho_req        double precision,
    IN    p_committed      boolean,
    IN    p_mechanism      varchar,
    IN    p_eps_equiv      double precision,
    IN    p_sensitivity    double precision DEFAULT 0,
    IN    p_sigma_or_scale double precision DEFAULT 0,
    INOUT p_status         varchar DEFAULT NULL
) LANGUAGE plpgsql AS $proc$
DECLARE
    v_total double precision;
    v_spent double precision;
    v_after double precision;
    v_op    varchar;
BEGIN
    SELECT rho_total, rho_spent INTO v_total, v_spent
      FROM sys_budget_account
     WHERE account_id = p_account_id
     FOR UPDATE;

    IF NOT FOUND THEN
        p_status := 'no_such_account';
        RETURN;
    END IF;

    -- Idempotency: a retried settle must not double-commit/abort
    IF EXISTS (SELECT 1 FROM sys_budget_ledger
                WHERE query_id = p_query_id AND op IN ('COMMIT', 'ABORT')) THEN
        p_status := 'already_settled';
        RETURN;
    END IF;

    IF p_committed THEN
        v_op := 'COMMIT';
        v_after := v_spent + p_rho_req;
        UPDATE sys_budget_account
           SET rho_reserved = GREATEST(rho_reserved - p_rho_req, 0),
               rho_spent    = rho_spent + p_rho_req,
               status = CASE WHEN rho_spent + p_rho_req >= v_total
                             THEN 'exhausted' ELSE status END
         WHERE account_id = p_account_id;
    ELSE
        v_op := 'ABORT';
        v_after := v_spent;
        UPDATE sys_budget_account
           SET rho_reserved = GREATEST(rho_reserved - p_rho_req, 0),
               -- Clear exhausted on abort if budget is restored
               status = CASE WHEN status = 'exhausted'
                              AND rho_spent < v_total
                             THEN 'active' ELSE status END
         WHERE account_id = p_account_id;
    END IF;

    INSERT INTO sys_budget_ledger(
        account_id, query_id, op, rho_delta, rho_after, eps_equiv,
        mechanism, sensitivity, sigma_or_scale, logged_at,
        prev_hash, entry_hash)
    VALUES (
        p_account_id, p_query_id, v_op,
        CASE WHEN p_committed THEN p_rho_req ELSE -p_rho_req END,
        v_after, p_eps_equiv, p_mechanism, p_sensitivity, p_sigma_or_scale,
        now(),
        decode(repeat('00', 32), 'hex'),
        decode(repeat('00', 32), 'hex'));

    p_status := lower(v_op);
END;
$proc$;
"""

REFILL_PROC = """
CREATE OR REPLACE PROCEDURE sp_refill_budget(
    IN    p_account_id integer,
    IN    p_rho_amount double precision,
    INOUT p_status     varchar DEFAULT NULL
) LANGUAGE plpgsql AS $proc$
DECLARE
    v_spent       double precision;
    v_window_type varchar;
    v_period_s    bigint;
    v_after       double precision;
BEGIN
    SELECT rho_spent, window_type, refill_period_s
      INTO v_spent, v_window_type, v_period_s
      FROM sys_budget_account
     WHERE account_id = p_account_id
     FOR UPDATE;

    IF NOT FOUND THEN
        p_status := 'no_such_account';
        RETURN;
    END IF;

    IF v_window_type <> 'refill' THEN
        p_status := 'not_refillable';
        RETURN;
    END IF;

    v_after := GREATEST(v_spent - p_rho_amount, 0);

    UPDATE sys_budget_account
       SET rho_spent    = v_after,
           status       = 'active',
           window_start = now()
     WHERE account_id = p_account_id;

    INSERT INTO sys_budget_ledger(
        account_id, query_id, op, rho_delta, rho_after, eps_equiv,
        mechanism, sensitivity, sigma_or_scale, logged_at,
        prev_hash, entry_hash)
    VALUES (
        p_account_id, NULL, 'REFILL', -p_rho_amount,
        v_after, 0, 'refill', 0, 0, now(),
        decode(repeat('00', 32), 'hex'),
        decode(repeat('00', 32), 'hex'));

    p_status := 'refilled';
END;
$proc$;
"""
REFILL_PROC_DROP = "DROP PROCEDURE IF EXISTS sp_refill_budget;"

NO_TRUNCATE_TRIGGER = """
CREATE OR REPLACE FUNCTION privguard_ledger_no_truncate() RETURNS trigger AS $$
BEGIN
    IF COALESCE(current_setting('privguard.allow_ledger_truncate', true), 'off') <> 'on' THEN
        RAISE EXCEPTION 'sys_budget_ledger cannot be truncated (set privguard.allow_ledger_truncate)';
    END IF;
    RETURN NULL;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_no_truncate BEFORE TRUNCATE ON sys_budget_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION privguard_ledger_no_truncate();
"""
NO_TRUNCATE_DROP = """
DROP TRIGGER IF EXISTS trg_ledger_no_truncate ON sys_budget_ledger;
DROP FUNCTION IF EXISTS privguard_ledger_no_truncate();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0003_budget_constraints"),
        ("audit", "0001_initial"),
    ]

    operations = [
        # Procedures use CREATE OR REPLACE, so no explicit DROP needed
        migrations.RunSQL(RESERVE_V2, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(SETTLE_V2, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(REFILL_PROC, reverse_sql=REFILL_PROC_DROP),
        migrations.RunSQL(NO_TRUNCATE_TRIGGER, reverse_sql=NO_TRUNCATE_DROP),
    ]
