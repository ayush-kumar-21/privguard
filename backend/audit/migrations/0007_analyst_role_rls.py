"""
Database-level enforcement for analyst-facing access: role + column grants + RLS.

The Django gateway connects as the table owner and is unaffected. This role is
for everything else that might reach PostgreSQL with analyst credentials (a BI
tool, a leaked read-only connection string, a SQL-injection in a read path):

* NO privilege on the application tables (patients, visits, diagnoses, ...),
  on sys_noise_cache, the ledger, principals, grants or the exact-aggregate
  views (v_visit_summary). Raw data and noise material are unreachable.
* sys_query / sys_budget_account: SELECT on an explicit column list only, and
  row-level security limits them to the caller's own rows. The caller is
  identified by the *authenticated login role*, `session_user`, which must be
  named `analyst_<principal id>`. It deliberately does not use a settable session
  variable: a client with a raw SQL connection could simply `SET` it to someone
  else's id. `session_user` cannot be changed by a non-superuser, so the id is
  unforgeable. Any other login (or none) matches no row: default deny.
* Read-only: no INSERT / UPDATE / DELETE anywhere.

Provisioning one analyst (the id is encoded in the name, which is what RLS reads):
    CREATE ROLE analyst_42 LOGIN PASSWORD '...' IN ROLE privguard_analyst;

Policies only restrict members of privguard_analyst; a permissive
`app_unrestricted` policy keeps every other role (the app, migrations,
superusers) unaffected, so enabling RLS cannot lock the application out if it
runs as a non-owner.

Known limit: stored procedures keep PostgreSQL's default EXECUTE-to-PUBLIC, but
they run with the caller's rights, so for this role they fail on the first table
they touch. Revoking EXECUTE from PUBLIC would be tidier but could affect other
roles in a shared cluster, so it is left to the operator.

If the connecting user may not create roles (some managed PostgreSQL offerings)
the migration prints a NOTICE and does nothing, rather than failing the deploy.
"""
from django.db import migrations

FORWARD = r"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'privguard_analyst') THEN
        BEGIN
            CREATE ROLE privguard_analyst NOLOGIN;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE NOTICE 'cannot create role privguard_analyst; skipping DB-level analyst role';
            RETURN;
        END;
    END IF;

    -- Start from nothing, then grant the minimum.
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM privguard_analyst;
    GRANT USAGE ON SCHEMA public TO privguard_analyst;

    -- Explicit columns only: rows_scanned-style columns never appear here, and a
    -- later-added column is denied until someone grants it deliberately.
    GRANT SELECT (query_id, principal_id, db_id, fingerprint_id, sql_text, status,
                  rho_charged, delta_charged, submitted_at)
        ON sys_query TO privguard_analyst;
    GRANT SELECT (account_id, principal_id, db_id, rho_total, rho_spent, rho_reserved,
                  delta_target, window_type, status)
        ON sys_budget_account TO privguard_analyst;

    ALTER TABLE sys_query ENABLE ROW LEVEL SECURITY;
    ALTER TABLE sys_budget_account ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS app_unrestricted ON sys_query;
    DROP POLICY IF EXISTS analyst_own_rows ON sys_query;
    CREATE POLICY app_unrestricted ON sys_query FOR ALL TO PUBLIC
        USING (NOT pg_has_role(current_user, 'privguard_analyst', 'MEMBER'))
        WITH CHECK (NOT pg_has_role(current_user, 'privguard_analyst', 'MEMBER'));
    CREATE POLICY analyst_own_rows ON sys_query FOR SELECT TO privguard_analyst
        USING (principal_id = NULLIF(substring(session_user from '^analyst_([0-9]+)$'), '')::integer);

    DROP POLICY IF EXISTS app_unrestricted ON sys_budget_account;
    DROP POLICY IF EXISTS analyst_own_rows ON sys_budget_account;
    CREATE POLICY app_unrestricted ON sys_budget_account FOR ALL TO PUBLIC
        USING (NOT pg_has_role(current_user, 'privguard_analyst', 'MEMBER'))
        WITH CHECK (NOT pg_has_role(current_user, 'privguard_analyst', 'MEMBER'));
    CREATE POLICY analyst_own_rows ON sys_budget_account FOR SELECT TO privguard_analyst
        USING (principal_id = NULLIF(substring(session_user from '^analyst_([0-9]+)$'), '')::integer);
END
$$;
"""

REVERSE = r"""
DROP POLICY IF EXISTS analyst_own_rows ON sys_query;
DROP POLICY IF EXISTS app_unrestricted ON sys_query;
DROP POLICY IF EXISTS analyst_own_rows ON sys_budget_account;
DROP POLICY IF EXISTS app_unrestricted ON sys_budget_account;
ALTER TABLE sys_query DISABLE ROW LEVEL SECURITY;
ALTER TABLE sys_budget_account DISABLE ROW LEVEL SECURITY;
-- The role itself is left in place: dropping it requires revoking every grant first,
-- and an empty NOLOGIN role is harmless.
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0006_commit_noise_seeds"),
        ("budget", "0008_global_budget_cap"),
        ("accounts", "0002_grant_constraints"),
        ("catalog", "0003_dp_hardening"),
        ("safehealth", "0003_performance_indexes"),
    ]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
