"""Separation of duty, enforced by the database.

The per-patient ledger shows exactly which patients a query touched, so whoever may read it (the
`dba`) must not be able to run queries. The application refuses that combination, but the rule is
only as strong as its weakest entry point, so this trigger makes Postgres refuse it too: Django
admin, a script, or hand-written SQL cannot give one principal `dba` together with a role that
carries `dp_analyst` or `auditor`.

The role names are a literal list below (see QUERY_ROLES); `tests/test_separation_of_duty.py`
compares the deployed function with `accounts/roles.py` and fails if they ever differ.
"""
from django.db import migrations


# The roles that can run DP queries, frozen as of this migration: dp_analyst, auditor and the job
# roles that carry them. A migration must not be generated from application code that can change
# later, so when accounts/roles.py gains a query-carrying role, add a NEW migration that replaces
# this function. tests/test_separation_of_duty.py fails until you do.
QUERY_ROLES = (
    "auditor", "biostatistician", "business_analyst", "compliance_officer", "data_scientist",
    "dp_analyst", "epidemiologist", "internal_auditor", "privacy_officer", "researcher",
    "security_auditor",
)


def build_sql():
    array = ", ".join(f"'{n}'" for n in QUERY_ROLES)
    return f"""
CREATE OR REPLACE FUNCTION privguard_separation_of_duty() RETURNS trigger AS $sod$
DECLARE
    v_new   text;
    v_roles text[];
BEGIN
    SELECT role_name INTO v_new FROM sys_role WHERE role_id = NEW.role_id;
    SELECT coalesce(array_agg(r.role_name), ARRAY[]::text[]) INTO v_roles
      FROM sys_principal_role pr JOIN sys_role r ON r.role_id = pr.role_id
     WHERE pr.principal_id = NEW.principal_id AND pr.id IS DISTINCT FROM NEW.id;
    v_roles := v_roles || v_new;
    IF 'dba' = ANY(v_roles) AND v_roles && ARRAY[{array}]::text[] THEN
        RAISE EXCEPTION 'separation of duty: a principal cannot hold dba together with a query role (%)',
            array_to_string(v_roles, ', ');
    END IF;
    RETURN NEW;
END;
$sod$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_separation_of_duty ON sys_principal_role;
CREATE TRIGGER trg_separation_of_duty BEFORE INSERT OR UPDATE ON sys_principal_role
    FOR EACH ROW EXECUTE FUNCTION privguard_separation_of_duty();

-- Existing data is not rewritten; a conflict that already exists is reported, not hidden.
DO $check$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT p.username FROM sys_principal p
         WHERE EXISTS (SELECT 1 FROM sys_principal_role pr JOIN sys_role ro ON ro.role_id = pr.role_id
                        WHERE pr.principal_id = p.id AND ro.role_name = 'dba')
           AND EXISTS (SELECT 1 FROM sys_principal_role pr JOIN sys_role ro ON ro.role_id = pr.role_id
                        WHERE pr.principal_id = p.id AND ro.role_name = ANY(ARRAY[{array}]::text[]))
    LOOP
        RAISE WARNING 'separation of duty: % already holds dba and a query role; remove one', r.username;
    END LOOP;
END
$check$;
"""


DROP = """
DROP TRIGGER IF EXISTS trg_separation_of_duty ON sys_principal_role;
DROP FUNCTION IF EXISTS privguard_separation_of_duty();
"""


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_enterprise_roles")]
    # RunSQL passes no parameters, so the % in the error message is not read as a placeholder.
    operations = [migrations.RunSQL(build_sql(), DROP)]
