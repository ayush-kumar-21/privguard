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

    class Meta:
        db_table = "sys_budget_account"
        constraints = [
            models.UniqueConstraint(fields=["principal", "db"], name="uq_account_principal_db")
        ]

    @property
    def rho_remaining(self) -> float:
        return self.rho_total - self.rho_spent - self.rho_reserved


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
        indexes = [models.Index(fields=["account", "entry_id"], name="idx_ledger_account")]
