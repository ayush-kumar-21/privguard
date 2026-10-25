"""Per-patient privacy budget: tables, hash-chain and append-only triggers, charge/refund/verify
routines, a view, and a backfill for existing patients.

Why a second ledger: the analyst budget bounds what one person learns, but a patient's total loss is
the sum over every analyst who asked. This budget belongs to the patient, so opening another analyst
account never buys another budget. Charging rule (individual accounting, Feldman & Zrnic 2021):
only patients who contribute rows to a query are charged, and a patient who cannot afford the query
is left out of it. Both routines are fail-closed: a patient with no budget row is excluded.

Lock order, to stay deadlock-free: analyst account row -> patient rows in ascending patient_id.
"""
from django.db import migrations, models
import django.db.models.deletion

ZERO = "decode(repeat('00', 32), 'hex')"

FUNCTIONS = f"""
CREATE OR REPLACE FUNCTION fn_patient_payload(
    p_entry_id bigint, p_patient_id integer, p_query_id bigint, p_op varchar,
    p_rho_delta double precision, p_rho_after double precision, p_logged_at timestamptz
) RETURNS bytea LANGUAGE sql IMMUTABLE AS $fn$
    SELECT convert_to(concat_ws('|',
        p_entry_id::text, p_patient_id::text, coalesce(p_query_id::text, ''), p_op,
        p_rho_delta::text, p_rho_after::text,
        to_char(p_logged_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')), 'UTF8')
$fn$;

CREATE OR REPLACE FUNCTION privguard_patient_ledger_hash() RETURNS trigger AS $trg$
DECLARE
    v_prev bytea;
BEGIN
    SELECT entry_hash INTO v_prev FROM sys_patient_ledger
     WHERE patient_id = NEW.patient_id ORDER BY entry_id DESC LIMIT 1;
    IF v_prev IS NULL THEN
        v_prev := {ZERO};                       -- genesis
    END IF;
    NEW.prev_hash := v_prev;
    NEW.entry_hash := sha256(NEW.prev_hash || fn_patient_payload(
        NEW.entry_id, NEW.patient_id, NEW.query_id, NEW.op, NEW.rho_delta, NEW.rho_after, NEW.logged_at));
    RETURN NEW;
END;
$trg$ LANGUAGE plpgsql;

CREATE TRIGGER trg_patient_ledger_hash BEFORE INSERT ON sys_patient_ledger
    FOR EACH ROW EXECUTE FUNCTION privguard_patient_ledger_hash();

CREATE OR REPLACE FUNCTION privguard_patient_ledger_readonly() RETURNS trigger AS $ro$
BEGIN
    RAISE EXCEPTION 'sys_patient_ledger is append-only; % is not permitted', TG_OP;
END;
$ro$ LANGUAGE plpgsql;

CREATE TRIGGER trg_patient_ledger_readonly BEFORE UPDATE OR DELETE ON sys_patient_ledger
    FOR EACH ROW EXECUTE FUNCTION privguard_patient_ledger_readonly();
CREATE TRIGGER trg_patient_ledger_no_truncate BEFORE TRUNCATE ON sys_patient_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION privguard_patient_ledger_readonly();

-- Every new patient gets a budget row, so no patient can ever be missing one.
CREATE OR REPLACE FUNCTION privguard_patient_budget_init() RETURNS trigger AS $init$
BEGIN
    INSERT INTO sys_patient_budget(patient_id, rho_total, rho_spent, status)
    VALUES (NEW.patient_id,
            coalesce((SELECT default_patient_rho FROM sys_database WHERE db_name = 'safehealth'), 10.0),
            0, 'active')
    ON CONFLICT (patient_id) DO NOTHING;
    RETURN NEW;
END;
$init$ LANGUAGE plpgsql;

CREATE TRIGGER trg_patient_budget_init AFTER INSERT ON patients
    FOR EACH ROW EXECUTE FUNCTION privguard_patient_budget_init();

-- Charge `p_rho` to each listed patient who can afford it; return the ids that were left out
-- (cannot afford it, not active, or no budget row at all). One transaction with the caller.
CREATE OR REPLACE FUNCTION fn_charge_patients(
    p_query_id bigint, p_rho double precision, p_patient_ids integer[]
) RETURNS integer[] LANGUAGE plpgsql AS $charge$
DECLARE
    r          RECORD;
    v_excluded integer[] := '{{}}';
    v_after    double precision;
BEGIN
    IF p_rho IS NULL OR p_rho <= 0 THEN
        RAISE EXCEPTION 'fn_charge_patients: rho must be positive';
    END IF;

    FOR r IN
        SELECT patient_id, rho_total, rho_spent, status
          FROM sys_patient_budget
         WHERE patient_id = ANY(p_patient_ids)
         ORDER BY patient_id
           FOR UPDATE
    LOOP
        IF r.status <> 'active' OR r.rho_spent + p_rho > r.rho_total + 1e-9 THEN
            v_excluded := v_excluded || r.patient_id;
            CONTINUE;
        END IF;
        v_after := LEAST(r.rho_spent + p_rho, r.rho_total);
        UPDATE sys_patient_budget
           SET rho_spent = v_after,
               status = CASE WHEN v_after >= rho_total - 1e-9 THEN 'exhausted' ELSE status END
         WHERE patient_id = r.patient_id;
        INSERT INTO sys_patient_ledger(patient_id, query_id, op, rho_delta, rho_after, logged_at,
                                       prev_hash, entry_hash)
        VALUES (r.patient_id, p_query_id, 'CHARGE', p_rho, v_after, now(), {ZERO}, {ZERO});
    END LOOP;

    -- Fail closed: a listed patient with no budget row is never silently uncharged.
    v_excluded := v_excluded || coalesce((
        SELECT array_agg(x) FROM unnest(p_patient_ids) AS x
         WHERE NOT EXISTS (SELECT 1 FROM sys_patient_budget b WHERE b.patient_id = x)), '{{}}');
    RETURN v_excluded;
END;
$charge$;

-- Give back what fn_charge_patients took for a query that produced no answer. Idempotent.
CREATE OR REPLACE FUNCTION fn_refund_patients(p_query_id bigint) RETURNS integer
LANGUAGE plpgsql AS $refund$
DECLARE
    r       RECORD;
    v_after double precision;
    v_count integer := 0;
BEGIN
    FOR r IN
        SELECT c.patient_id, c.rho_delta
          FROM sys_patient_ledger c
         WHERE c.query_id = p_query_id AND c.op = 'CHARGE'
           AND NOT EXISTS (SELECT 1 FROM sys_patient_ledger f
                            WHERE f.query_id = p_query_id AND f.patient_id = c.patient_id
                              AND f.op = 'REFUND')
         ORDER BY c.patient_id
    LOOP
        UPDATE sys_patient_budget
           SET rho_spent = GREATEST(rho_spent - r.rho_delta, 0),
               status = CASE WHEN status = 'exhausted' AND rho_spent - r.rho_delta < rho_total - 1e-9
                             THEN 'active' ELSE status END
         WHERE patient_id = r.patient_id
        RETURNING rho_spent INTO v_after;
        INSERT INTO sys_patient_ledger(patient_id, query_id, op, rho_delta, rho_after, logged_at,
                                       prev_hash, entry_hash)
        VALUES (r.patient_id, p_query_id, 'REFUND', -r.rho_delta, v_after, now(), {ZERO}, {ZERO});
        v_count := v_count + 1;
    END LOOP;
    RETURN v_count;
END;
$refund$;

-- Replay one patient's chain, as fn_verify_ledger does for an analyst's.
CREATE OR REPLACE FUNCTION fn_verify_patient_chain(p_patient_id integer)
RETURNS TABLE(ok boolean, entries bigint, first_bad_entry bigint)
LANGUAGE plpgsql AS $verify$
DECLARE
    r       RECORD;
    v_prev  bytea := {ZERO};
    v_count bigint := 0;
BEGIN
    FOR r IN SELECT * FROM sys_patient_ledger WHERE patient_id = p_patient_id ORDER BY entry_id LOOP
        v_count := v_count + 1;
        IF r.prev_hash <> v_prev OR r.entry_hash <> sha256(v_prev || fn_patient_payload(
               r.entry_id, r.patient_id, r.query_id, r.op, r.rho_delta, r.rho_after, r.logged_at)) THEN
            RETURN QUERY SELECT false, v_count, r.entry_id;
            RETURN;
        END IF;
        v_prev := r.entry_hash;
    END LOOP;
    RETURN QUERY SELECT true, v_count, NULL::bigint;
END;
$verify$;

REVOKE ALL ON FUNCTION fn_charge_patients(bigint, double precision, integer[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION fn_refund_patients(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION fn_verify_patient_chain(integer) FROM PUBLIC;
"""

FUNCTIONS_DROP = """
DROP TRIGGER IF EXISTS trg_patient_budget_init ON patients;
DROP FUNCTION IF EXISTS privguard_patient_budget_init();
DROP FUNCTION IF EXISTS fn_verify_patient_chain(integer);
DROP FUNCTION IF EXISTS fn_refund_patients(bigint);
DROP FUNCTION IF EXISTS fn_charge_patients(bigint, double precision, integer[]);
DROP TRIGGER IF EXISTS trg_patient_ledger_no_truncate ON sys_patient_ledger;
DROP TRIGGER IF EXISTS trg_patient_ledger_readonly ON sys_patient_ledger;
DROP TRIGGER IF EXISTS trg_patient_ledger_hash ON sys_patient_ledger;
DROP FUNCTION IF EXISTS privguard_patient_ledger_readonly();
DROP FUNCTION IF EXISTS privguard_patient_ledger_hash();
DROP FUNCTION IF EXISTS fn_patient_payload(bigint, integer, bigint, varchar, double precision, double precision, timestamptz);
"""

BACKFILL = """
INSERT INTO sys_patient_budget(patient_id, rho_total, rho_spent, status)
SELECT p.patient_id,
       coalesce((SELECT default_patient_rho FROM sys_database WHERE db_name = 'safehealth'), 10.0),
       0, 'active'
  FROM patients p
ON CONFLICT (patient_id) DO NOTHING;
"""

VIEW = """
CREATE OR REPLACE VIEW v_patient_budget AS
SELECT b.patient_id, b.rho_total, b.rho_spent,
       GREATEST(b.rho_total - b.rho_spent, 0) AS rho_remaining,
       b.status,
       count(l.entry_id) FILTER (WHERE l.op = 'CHARGE') AS charges,
       max(l.logged_at) FILTER (WHERE l.op = 'CHARGE')  AS last_charged_at
  FROM sys_patient_budget b
  LEFT JOIN sys_patient_ledger l ON l.patient_id = b.patient_id
 GROUP BY b.patient_id, b.rho_total, b.rho_spent, b.status;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("budget", "0009_query_quota"),
        ("catalog", "0004_default_patient_rho"),
        ("safehealth", "0003_performance_indexes"),
        ("audit", "0008_noise_seed_help"),
    ]

    operations = [
        migrations.CreateModel(
            name="PatientBudget",
            fields=[
                ("patient", models.OneToOneField(
                    db_column="patient_id", on_delete=django.db.models.deletion.PROTECT,
                    primary_key=True, related_name="privacy_budget", serialize=False,
                    to="safehealth.patient")),
                ("rho_total", models.FloatField(help_text="zCDP allowance for this patient")),
                ("rho_spent", models.FloatField(default=0.0, help_text="charged so far (never refunded by erasure)")),
                ("status", models.CharField(
                    choices=[("active", "active"), ("exhausted", "exhausted"), ("erased", "erased")],
                    default="active", max_length=12)),
            ],
            options={"db_table": "sys_patient_budget"},
        ),
        migrations.CreateModel(
            name="PatientLedger",
            fields=[
                ("entry_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("op", models.CharField(choices=[("CHARGE", "CHARGE"), ("REFUND", "REFUND")], max_length=10)),
                ("rho_delta", models.FloatField(help_text="signed: + charge, - refund")),
                ("rho_after", models.FloatField(help_text="the patient's rho_spent after this entry")),
                ("logged_at", models.DateTimeField()),
                ("prev_hash", models.BinaryField(max_length=32)),
                ("entry_hash", models.BinaryField(max_length=32)),
                ("patient", models.ForeignKey(
                    db_column="patient_id", on_delete=django.db.models.deletion.PROTECT,
                    related_name="privacy_ledger", to="safehealth.patient")),
                ("query", models.ForeignKey(
                    blank=True, db_column="query_id", null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="patient_ledger_entries", to="audit.sysquery")),
            ],
            options={"db_table": "sys_patient_ledger"},
        ),
        migrations.AddIndex(
            model_name="patientledger",
            index=models.Index(fields=["patient", "entry_id"], name="idx_patient_ledger")),
        migrations.AddConstraint(
            model_name="patientbudget",
            constraint=models.CheckConstraint(condition=models.Q(rho_total__gt=0), name="ck_patient_rho_total_pos")),
        migrations.AddConstraint(
            model_name="patientbudget",
            constraint=models.CheckConstraint(condition=models.Q(rho_spent__gte=0), name="ck_patient_rho_spent_nonneg")),
        migrations.AddConstraint(
            model_name="patientbudget",
            constraint=models.CheckConstraint(
                condition=models.Q(rho_spent__lte=models.F("rho_total")), name="ck_patient_never_oversold")),
        migrations.RunSQL(FUNCTIONS, FUNCTIONS_DROP),
        migrations.RunSQL(BACKFILL, migrations.RunSQL.noop),
        migrations.RunSQL(VIEW, "DROP VIEW IF EXISTS v_patient_budget;"),
        migrations.CreateModel(
            name="PatientBudgetView",
            fields=[
                ("patient_id", models.IntegerField(primary_key=True, serialize=False)),
                ("rho_total", models.FloatField()),
                ("rho_spent", models.FloatField()),
                ("rho_remaining", models.FloatField()),
                ("status", models.CharField(max_length=12)),
                ("charges", models.BigIntegerField()),
                ("last_charged_at", models.DateTimeField(null=True)),
            ],
            options={"db_table": "v_patient_budget", "managed": False},
        ),
    ]
