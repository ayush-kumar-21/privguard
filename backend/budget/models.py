"""
Privacy Budget Ledger (Subsystem D).

SYS_BUDGET_ACCOUNT holds the per-principal, per-database zCDP allocation and the
materialised running spend. SYS_BUDGET_LEDGER is the append-only, hash-chained
record of every RESERVE / COMMIT / ABORT / REFILL — a weak entity that has no
meaning outside its owning account.

Integrity is enforced in the database (not just here): a BEFORE INSERT trigger
computes `entry_hash` from `prev_hash || row`, and BEFORE UPDATE/DELETE triggers
RAISE EXCEPTION to keep the ledger append-only. See migrations for the PL/pgSQL.
"""
from django.conf import settings
from django.db import models


class BudgetAccount(models.Model):
    WINDOW_TYPES = [("lifetime", "lifetime"), ("rolling", "rolling"), ("refill", "refill")]
    STATUS_CHOICES = [("active", "active"), ("exhausted", "exhausted"), ("suspended", "suspended")]

    account_id = models.AutoField(primary_key=True)
    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        db_column="principal_id", related_name="budget_accounts",
    )
    db = models.ForeignKey(
        "catalog.SysDatabase", on_delete=models.CASCADE,
        db_column="db_id", related_name="budget_accounts",
    )
    rho_total = models.FloatField(help_text="zCDP budget cap")
    rho_spent = models.FloatField(default=0.0, help_text="materialised aggregate over the ledger")
    rho_reserved = models.FloatField(default=0.0, help_text="in-flight holds")
    delta_target = models.FloatField()
    window_type = models.CharField(max_length=12, choices=WINDOW_TYPES, default="lifetime")
    window_start = models.DateTimeField(auto_now_add=True)
    refill_period_s = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="active")
    # Run quota, set by the administrator. Every answered query (new or cached) uses one run;
    # NULL means unlimited. Read afresh on each query, so a change applies to the very next run.
    max_queries = models.PositiveIntegerField(
        null=True, blank=True, help_text="runs allowed; empty = unlimited"
    )
    queries_used = models.PositiveIntegerField(default=0, help_text="runs used so far (counted even when unlimited)")

    class Meta:
        db_table = "sys_budget_account"
        constraints = [
            models.UniqueConstraint(fields=["principal", "db"], name="uq_account_principal_db"),
            models.CheckConstraint(condition=models.Q(rho_total__gt=0), name="ck_account_rho_total_pos"),
            models.CheckConstraint(condition=models.Q(rho_spent__gte=0), name="ck_account_rho_spent_nonneg"),
            models.CheckConstraint(condition=models.Q(rho_reserved__gte=0), name="ck_account_rho_reserved_nonneg"),
            models.CheckConstraint(
                condition=models.Q(rho_spent__lte=models.F("rho_total") - models.F("rho_reserved")),
                name="ck_account_never_oversold",
            ),
            models.CheckConstraint(
                condition=models.Q(delta_target__gt=0) & models.Q(delta_target__lt=1),
                name="ck_account_delta_in_unit_interval",
            ),
            models.CheckConstraint(
                condition=~models.Q(window_type="refill") | models.Q(refill_period_s__gt=0),
                name="ck_account_refill_needs_period",
            ),
        ]

    @property
    def rho_remaining(self) -> float:
        return self.rho_total - self.rho_spent - self.rho_reserved

    @property
    def queries_remaining(self) -> int | None:
        """Runs left, or None when the quota is unlimited."""
        if self.max_queries is None:
            return None
        return max(0, self.max_queries - self.queries_used)


class BudgetLedger(models.Model):
    OP_CHOICES = [
        ("RESERVE", "RESERVE"),
        ("COMMIT", "COMMIT"),
        ("ABORT", "ABORT"),
        ("REFILL", "REFILL"),
    ]
    entry_id = models.BigAutoField(primary_key=True, help_text="monotonic, == LSN")
    account = models.ForeignKey(
        BudgetAccount, on_delete=models.CASCADE,
        db_column="account_id", related_name="ledger_entries",
    )
    query = models.ForeignKey(
        "audit.SysQuery", on_delete=models.PROTECT,
        db_column="query_id", null=True, blank=True, related_name="ledger_entries",
    )
    op = models.CharField(max_length=10, choices=OP_CHOICES)
    rho_delta = models.FloatField(help_text="signed")
    rho_after = models.FloatField()
    eps_equiv = models.FloatField(help_text="eps at delta_target, for humans")
    mechanism = models.CharField(max_length=20)
    sensitivity = models.FloatField()
    sigma_or_scale = models.FloatField()
    logged_at = models.DateTimeField(auto_now_add=True)
    prev_hash = models.BinaryField(max_length=32)
    entry_hash = models.BinaryField(max_length=32)

    class Meta:
        db_table = "sys_budget_ledger"
        indexes = [
            models.Index(fields=["account", "entry_id"], name="idx_ledger_account"),
            # fn_reconcile_account's NOT EXISTS
            models.Index(fields=["query"], name="idx_ledger_query_id"),
            # burn-down chart time-range scans
            models.Index(fields=["logged_at"], name="idx_ledger_logged_at"),
        ]


class PatientBudget(models.Model):
    """The privacy budget of one PATIENT (the privacy unit), across every analyst.

    The analyst budget (`BudgetAccount`) says how much one person may learn; this says how much the
    data may be asked about one patient in total, however many accounts ask. Rows are created by a
    trigger on `patients`; spending goes only through `fn_charge_patients` / `fn_refund_patients`.
    """
    STATUS_CHOICES = [("active", "active"), ("exhausted", "exhausted"), ("erased", "erased")]

    patient = models.OneToOneField(
        "safehealth.Patient", primary_key=True, on_delete=models.PROTECT,
        db_column="patient_id", related_name="privacy_budget",
    )
    rho_total = models.FloatField(help_text="zCDP allowance for this patient")
    rho_spent = models.FloatField(default=0.0, help_text="charged so far (never refunded by erasure)")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="active")

    class Meta:
        db_table = "sys_patient_budget"
        constraints = [
            models.CheckConstraint(condition=models.Q(rho_total__gt=0), name="ck_patient_rho_total_pos"),
            models.CheckConstraint(condition=models.Q(rho_spent__gte=0), name="ck_patient_rho_spent_nonneg"),
            models.CheckConstraint(
                condition=models.Q(rho_spent__lte=models.F("rho_total")), name="ck_patient_never_oversold"),
        ]

    @property
    def rho_remaining(self) -> float:
        return max(0.0, self.rho_total - self.rho_spent)


class PatientLedger(models.Model):
    """Append-only, hash-chained (per patient) record of every charge and refund."""
    OP_CHOICES = [("CHARGE", "CHARGE"), ("REFUND", "REFUND")]

    entry_id = models.BigAutoField(primary_key=True)
    patient = models.ForeignKey(
        "safehealth.Patient", on_delete=models.PROTECT,
        db_column="patient_id", related_name="privacy_ledger",
    )
    query = models.ForeignKey(
        "audit.SysQuery", on_delete=models.PROTECT,
        db_column="query_id", null=True, blank=True, related_name="patient_ledger_entries",
    )
    op = models.CharField(max_length=10, choices=OP_CHOICES)
    rho_delta = models.FloatField(help_text="signed: + charge, - refund")
    rho_after = models.FloatField(help_text="the patient's rho_spent after this entry")
    logged_at = models.DateTimeField()
    prev_hash = models.BinaryField(max_length=32)
    entry_hash = models.BinaryField(max_length=32)

    class Meta:
        db_table = "sys_patient_ledger"
        indexes = [models.Index(fields=["patient", "entry_id"], name="idx_patient_ledger")]


class PatientBudgetView(models.Model):
    """Read-only mapping of the view `v_patient_budget`."""
    patient_id = models.IntegerField(primary_key=True)
    rho_total = models.FloatField()
    rho_spent = models.FloatField()
    rho_remaining = models.FloatField()
    status = models.CharField(max_length=12)
    charges = models.BigIntegerField()
    last_charged_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = "v_patient_budget"
