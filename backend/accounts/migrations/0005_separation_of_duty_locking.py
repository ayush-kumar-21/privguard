"""Make the separation-of-duty trigger race-free, and re-check it when a role is renamed.

Two concurrent transactions could each add one role (dba / a query role) to the same principal: each
trigger saw only the other's *committed* rows, so both passed and the principal ended up holding
both. The trigger now takes a transaction-scoped advisory lock keyed on the principal first, so
role changes for one principal run one at a time, and the second one sees the first's committed row.

Renaming a role (for example clinician -> dba) could also create a conflict without touching
sys_principal_role, so a trigger on sys_role re-checks every principal after a rename.
"""
from django.db import migrations

# Frozen, like 0004: a new query-carrying role needs a new migration, not an edit to this one.
QUERY_ROLES = (
    "auditor", "biostatistician", "business_analyst", "compliance_officer", "data_scientist",
    "dp_analyst", "epidemiologist", "internal_auditor", "privacy_officer", "researcher",
    "security_auditor",
)
ARRAY = ", ".join(f"'{n}'" for n in QUERY_ROLES)

SQL = f"""
CREATE OR REPLACE FUNCTION privguard_separation_of_duty() RETURNS trigger AS $sod$
DECLARE
    v_new   text;
    v_roles text[];
BEGIN
    -- Serialise role changes for this principal; later statements then see earlier commits.
    PERFORM pg_advisory_xact_lock(hashtext('privguard_sod'), NEW.principal_id::integer);

    SELECT role_name INTO v_new FROM sys_role WHERE role_id = NEW.role_id;
    SELECT coalesce(array_agg(r.role_name), ARRAY[]::text[]) INTO v_roles
      FROM sys_principal_role pr JOIN sys_role r ON r.role_id = pr.role_id
     WHERE pr.principal_id = NEW.principal_id AND pr.id IS DISTINCT FROM NEW.id;
    v_roles := v_roles || v_new;
    IF 'dba' = ANY(v_roles) AND v_roles && ARRAY[{ARRAY}]::text[] THEN
        RAISE EXCEPTION 'separation of duty: a principal cannot hold dba together with a query role (%)',
            array_to_string(v_roles, ', ');
    END IF;
    RETURN NEW;
END;
$sod$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION privguard_separation_of_duty_rename() RETURNS trigger AS $ren$
DECLARE
    v_name text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('privguard_sod'), 0);
    SELECT p.username INTO v_name FROM sys_principal p
     WHERE EXISTS (SELECT 1 FROM sys_principal_role pr JOIN sys_role r ON r.role_id = pr.role_id
                    WHERE pr.principal_id = p.id AND r.role_name = 'dba')
       AND EXISTS (SELECT 1 FROM sys_principal_role pr JOIN sys_role r ON r.role_id = pr.role_id
                    WHERE pr.principal_id = p.id AND r.role_name = ANY(ARRAY[{ARRAY}]::text[]))
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'separation of duty: renaming role % to % would leave % holding dba and a query role',
            OLD.role_name, NEW.role_name, v_name;
    END IF;
    RETURN NULL;
END;
$ren$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_separation_of_duty_rename ON sys_role;
CREATE TRIGGER trg_separation_of_duty_rename AFTER UPDATE OF role_name ON sys_role
    FOR EACH ROW EXECUTE FUNCTION privguard_separation_of_duty_rename();
"""

DROP = """
DROP TRIGGER IF EXISTS trg_separation_of_duty_rename ON sys_role;
DROP FUNCTION IF EXISTS privguard_separation_of_duty_rename();
"""


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_separation_of_duty")]
    # RunSQL passes no parameters, so the % in the messages is not read as a placeholder.
    operations = [migrations.RunSQL(SQL, DROP)]
