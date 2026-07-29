"""
Query Audit & Rewriter State (Subsystem E).

SYS_QUERY is the per-query audit log; SYS_QUERY_FINGERPRINT canonicalises the
AST for deduplication; SYS_NOISE_CACHE memoises noisy answers (defeats the
averaging attack); SYS_PREDICATE_OVERLAP is a unary/recursive association over
fingerprints for differencing-attack detection.
"""
from django.conf import settings
from django.db import models


class SysQueryFingerprint(models.Model):
    fingerprint_id = models.BigAutoField(primary_key=True)
    ast_hash = models.BinaryField(max_length=32, unique=True, help_text="BLAKE3 of canonical AST")
    table = models.ForeignKey(
        "catalog.SysTable", on_delete=models.CASCADE,
        db_column="table_id", related_name="fingerprints",
    )
    target_column = models.ForeignKey(
        "catalog.SysColumn", on_delete=models.SET_NULL,
        db_column="target_column_id", null=True, blank=True,
        related_name="fingerprints",
    )
    agg_kind = models.CharField(max_length=12, help_text="COUNT|SUM|AVG|HISTOGRAM")
    predicate_sig = models.CharField(max_length=1024, help_text="normalised CNF of WHERE")
    groupby_sig = models.CharField(max_length=512, null=True, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    times_seen = models.IntegerField(default=1)

    class Meta:
        db_table = "sys_query_fingerprint"
        indexes = [
            # fingerprint analytics
            models.Index(fields=["table", "agg_kind"], name="idx_fp_table_agg"),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(times_seen__gte=1), name="ck_fingerprint_times_seen_pos"),
        ]


class SysQuery(models.Model):
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("executed", "executed"),
        ("cache_hit", "cache_hit"),
        ("error", "error"),
        ("rejected_budget", "rejected_budget"),
        ("rejected_policy", "rejected_policy"),
        ("rejected_correlation", "rejected_correlation"),
        ("suppressed_partition", "suppressed_partition"),
    ]
    query_id = models.BigAutoField(primary_key=True)
    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        db_column="principal_id", related_name="queries",
    )
    db = models.ForeignKey(
        "catalog.SysDatabase", on_delete=models.PROTECT,
        db_column="db_id", related_name="queries",
    )
    fingerprint = models.ForeignKey(
        SysQueryFingerprint, on_delete=models.SET_NULL,
        db_column="fingerprint_id", null=True, blank=True,
        related_name="queries",
    )
    sql_text = models.CharField(max_length=4000)
    plan_json = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES)
    rho_charged = models.FloatField(default=0.0)
    rows_scanned = models.BigIntegerField(null=True, blank=True)
    latency_ms = models.FloatField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_query"
        verbose_name_plural = "queries"
        indexes = [
            models.Index(fields=["principal", "submitted_at"], name="idx_query_principal"),
            # QueryAuditViewSet filter+order
            models.Index(fields=["status", "-submitted_at"], name="idx_query_status_submitted"),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(rho_charged__gte=0), name="ck_query_rho_nonneg"),
        ]


class SysNoiseCache(models.Model):
    """Weak entity dependent on the fingerprint. Memoised noisy answer."""
    cache_id = models.BigAutoField(primary_key=True)
    fingerprint = models.ForeignKey(
        SysQueryFingerprint, on_delete=models.CASCADE,
        db_column="fingerprint_id", related_name="noise_cache",
    )
    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        db_column="principal_id", related_name="noise_cache",
    )
    noise_seed = models.BinaryField(max_length=32, help_text="ChaCha20 seed, derived deterministically")
    cached_result = models.JSONField()
    rho_charged = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_noise_cache"
        constraints = [
            models.UniqueConstraint(
                fields=["fingerprint", "principal"], name="uq_noise_cache_fp_principal"
            )
        ]


class SysPredicateOverlap(models.Model):
    """Recursive M:N over fingerprints — pairwise comparison for differencing detection."""
    ACTIONS = [("allow", "allow"), ("surcharge", "surcharge"), ("block", "block")]
    overlap_id = models.BigAutoField(primary_key=True)
    fp_a = models.ForeignKey(
        SysQueryFingerprint, on_delete=models.CASCADE,
        db_column="fp_a", related_name="overlaps_as_a",
    )
    fp_b = models.ForeignKey(
        SysQueryFingerprint, on_delete=models.CASCADE,
        db_column="fp_b", related_name="overlaps_as_b",
    )
    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        db_column="principal_id", related_name="predicate_overlaps",
    )
    jaccard_est = models.FloatField()
    symmetric_diff_est = models.BigIntegerField()
    action_taken = models.CharField(max_length=20, choices=ACTIONS)
    detected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_predicate_overlap"
        constraints = [
            models.UniqueConstraint(
                fields=["fp_a", "fp_b", "principal"], name="uq_overlap_pair_principal"
            ),
            models.CheckConstraint(check=models.Q(jaccard_est__gte=0) & models.Q(jaccard_est__lte=1),
                                   name="ck_overlap_jaccard_unit"),
            models.CheckConstraint(check=models.Q(symmetric_diff_est__gte=0), name="ck_overlap_symdiff_nonneg"),
            models.CheckConstraint(check=models.Q(fp_a__lt=models.F("fp_b")), name="ck_overlap_canonical_order"),
        ]
