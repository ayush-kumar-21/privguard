"""
Global per-database budget cap, enforced inside sp_reserve_budget.

A per-analyst budget does not bound the *total* leakage of a database: K
colluding analysts (or one analyst holding K accounts) each get an independent
noisy answer to the same question and can average the noise away. The cap
bounds the sum of rho (spent + reserved) over every account of a database
(catalog.SysDatabase.rho_global_cap, NULL = unlimited).

The database row is locked FOR UPDATE after the account row, so reservations by
different analysts serialise on the cap and cannot race past it. Lock order is
always account -> database; sp_settle_budget never takes the database lock.
"""
from django.db import migrations

RESERVE_V3 = """
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
    v_db       integer;
    v_cap      double precision;
    v_used     double precision;
BEGIN
    -- Lock the account row: concurrent reservations serialise here.
    SELECT rho_total, rho_spent, rho_reserved, status, principal_id, db_id
      INTO v_total, v_spent, v_reserved, v_status, v_owner, v_db
      FROM sys_budget_account
     WHERE account_id = p_account_id
     FOR UPDATE;

    IF NOT FOUND THEN
        p_status := 'no_such_account';
        RETURN;
    END IF;

    IF v_status <> 'active' THEN
        p_status := 'account_' || v_status;
        RETURN;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM sys_query q
                    WHERE q.query_id = p_query_id AND q.principal_id = v_owner) THEN
        p_status := 'principal_mismatch';
        RETURN;
    END IF;

    IF EXISTS (SELECT 1 FROM sys_budget_ledger
                WHERE query_id = p_query_id AND op = 'RESERVE') THEN
        p_status := 'already_reserved';
        RETURN;
    END IF;

    IF v_spent + v_reserved + p_rho_req > v_total THEN
        p_status := 'rejected_budget';
        RETURN;
    END IF;

    -- Global cap: serialise on the database row, then compare the sum over
    -- every account of this database (this account's pending hold included).
    SELECT rho_global_cap INTO v_cap
      FROM sys_database
     WHERE db_id = v_db
     FOR UPDATE;

    IF v_cap IS NOT NULL THEN
        SELECT COALESCE(SUM(rho_spent + rho_reserved), 0) INTO v_used
          FROM sys_budget_account
         WHERE db_id = v_db;
        IF v_used + p_rho_req > v_cap THEN
            p_status := 'rejected_global_cap';
            RETURN;
        END IF;
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


class Migration(migrations.Migration):
    dependencies = [
        ("budget", "0007_budget_account_check_constraints"),
        ("catalog", "0003_dp_hardening"),
    ]

    operations = [
        # Reverse restores v2 by re-running its definition.
        migrations.RunSQL(RESERVE_V3, reverse_sql=migrations.RunSQL.noop),
    ]
