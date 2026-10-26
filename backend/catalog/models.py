"""
Structural Catalog (Subsystem A) and Privacy Policy (Subsystem B).

These `sys_*` tables are PrivGuard's self-describing system catalog. Under the
course build they are ordinary PostgreSQL tables managed by the Django ORM;
the `root_page_id` / `n_pages` / `height` columns are kept purely so the
physical schema matches the ER model 1:1 (Figures 1 & 5) — Postgres itself
owns the real storage.

Type mapping from the ER DDL: DOUBLE -> FloatField, INT PK -> AutoField,
BIGINT PK -> BigAutoField, VARBINARY/BINARY -> BinaryField.
"""
from django.db import models


class SysDatabase(models.Model):
    db_id = models.AutoField(primary_key=True)
    db_name = models.CharField(max_length=64, unique=True)
    dp_model = models.CharField(
        max_length=16,
        choices=[("bounded", "bounded"), ("unbounded", "unbounded")],
        default="bounded",
    )
    default_rho = models.FloatField(help_text="Default zCDP budget per account")
    default_delta = models.FloatField(help_text="e.g. 1e-6, must be << 1/N")
    default_patient_rho = models.FloatField(
        default=10.0,
        help_text="zCDP allowance of each PATIENT across all analysts (sys_patient_budget). "
                  "Unlike rho_global_cap it is per privacy unit, so one popular patient "
                  "cannot use up the allowance of everyone else.",
    )
    rho_global_cap = models.FloatField(
        null=True, blank=True,
        help_text="Cap on total rho (spent + reserved) across ALL accounts of this "
                  "database. Bounds the damage from colluding analysts; NULL = no cap.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_database"
        verbose_name = "database"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rho_global_cap__isnull=True) | models.Q(rho_global_cap__gt=0),
                name="ck_database_global_cap_pos",
            ),
            models.CheckConstraint(
                condition=models.Q(default_patient_rho__gt=0),
                name="ck_database_patient_rho_pos",
            ),
        ]

    def __str__(self):
        return self.db_name


class SysTable(models.Model):
    table_id = models.AutoField(primary_key=True)
    db = models.ForeignKey(
        SysDatabase, on_delete=models.CASCADE,
        db_column="db_id", related_name="tables",
    )
    table_name = models.CharField(max_length=64)
    root_page_id = models.IntegerField(default=0)
    n_pages = models.IntegerField(default=0)
    est_row_count = models.BigIntegerField(default=0)
    is_system = models.BooleanField(default=False)
    # Nullable self-referential catalog pointer: which column is the privacy unit.
    privacy_unit_col = models.ForeignKey(
        "catalog.SysColumn", on_delete=models.SET_NULL,
        db_column="privacy_unit_col", null=True, blank=True,
        related_name="privacy_unit_for_tables",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_table"
        verbose_name = "table"
        constraints = [
            models.UniqueConstraint(
                fields=["db", "table_name"], name="uq_table_db_name"
            )
        ]

    def __str__(self):
        return self.table_name


class SysColumn(models.Model):
    column_id = models.AutoField(primary_key=True)
    table = models.ForeignKey(
        SysTable, on_delete=models.CASCADE,
        db_column="table_id", related_name="columns",
    )
    col_name = models.CharField(max_length=64)
    ordinal = models.IntegerField()
    data_type = models.CharField(
        max_length=24,
        help_text="INT|BIGINT|DOUBLE|VARCHAR|DATE|TIMESTAMP|BOOL",
    )
    type_len = models.IntegerField(null=True, blank=True)
    is_nullable = models.BooleanField(default=True)
    is_privacy_unit = models.BooleanField(default=False)
    is_encrypted = models.BooleanField(default=False)

    class Meta:
        db_table = "sys_column"
        verbose_name = "column"
        constraints = [
            models.UniqueConstraint(
                fields=["table", "col_name"], name="uq_col_table_name"
            ),
            models.UniqueConstraint(
                fields=["table", "ordinal"], name="uq_col_table_ordinal"
            ),
            models.CheckConstraint(condition=models.Q(ordinal__gte=1), name="ck_column_ordinal_pos"),
        ]

    def __str__(self):
        return f"{self.table.table_name}.{self.col_name}"


class SysIndex(models.Model):
    index_id = models.AutoField(primary_key=True)
    table = models.ForeignKey(
        SysTable, on_delete=models.CASCADE,
        db_column="table_id", related_name="indexes",
    )
    index_name = models.CharField(max_length=64, unique=True)
    root_page_id = models.IntegerField(default=0)
    index_type = models.CharField(max_length=16, default="btree")
    is_unique = models.BooleanField(default=False)
    height = models.IntegerField(default=1)

    class Meta:
        db_table = "sys_index"
        verbose_name = "index"

    def __str__(self):
        return self.index_name


class SysIndexColumn(models.Model):
    """Associative entity resolving SYS_INDEX M:N SYS_COLUMN.

    The ER models the key as composite (index_id, ordinal); realised here as a
    surrogate PK plus a UNIQUE constraint, the standard Django idiom.
    """
    index = models.ForeignKey(
        SysIndex, on_delete=models.CASCADE,
        db_column="index_id", related_name="index_columns",
    )
    column = models.ForeignKey(
        SysColumn, on_delete=models.CASCADE,
        db_column="column_id", related_name="index_columns",
    )
    ordinal = models.IntegerField()

    class Meta:
        db_table = "sys_index_column"
        constraints = [
            models.UniqueConstraint(
                fields=["index", "ordinal"], name="pk_index_column"
            )
        ]


# --------------------------------------------------------------------------
# Subsystem B — Privacy Policy
# --------------------------------------------------------------------------
class SysTablePrivacy(models.Model):
    """1:1 extension of SYS_TABLE declaring the privacy unit + contribution bounds."""
    table = models.OneToOneField(
        SysTable, on_delete=models.CASCADE,
        db_column="table_id", primary_key=True, related_name="privacy",
    )
    privacy_unit_col = models.ForeignKey(
        SysColumn, on_delete=models.PROTECT,
        db_column="privacy_unit_col", related_name="privacy_unit_policies",
    )
    max_rows_per_unit = models.IntegerField(default=1, help_text="L0 / contribution bound")
    max_groups_per_unit = models.IntegerField(default=1)
    enforce_dp = models.BooleanField(default=True)
    min_partition_rho = models.FloatField(default=0.01)

    class Meta:
        db_table = "sys_table_privacy"
        verbose_name_plural = "table privacy policies"
        constraints = [
            models.CheckConstraint(condition=models.Q(max_rows_per_unit__gte=1), name="ck_tabpriv_max_rows_pos"),
            models.CheckConstraint(condition=models.Q(max_groups_per_unit__gte=1), name="ck_tabpriv_max_groups_pos"),
            models.CheckConstraint(condition=models.Q(min_partition_rho__gt=0), name="ck_tabpriv_min_rho_pos"),
        ]


class SysColumnPrivacy(models.Model):
    MECHANISMS = [
        ("laplace", "laplace"),
        ("gaussian", "gaussian"),
        ("discrete_laplace", "discrete_laplace"),
        ("none", "none"),
    ]
    policy_id = models.AutoField(primary_key=True)
    column = models.OneToOneField(
        SysColumn, on_delete=models.CASCADE,
        db_column="column_id", related_name="privacy",
    )
    mechanism = models.CharField(max_length=20, choices=MECHANISMS)
    lower_clamp = models.FloatField(null=True, blank=True, help_text="required for SUM/AVG")
    upper_clamp = models.FloatField(null=True, blank=True)
    max_contrib_per_unit = models.IntegerField(default=1, help_text="L_inf bound")
    is_groupable = models.BooleanField(default=False)
    is_filterable = models.BooleanField(default=True)
    min_partition_size = models.IntegerField(default=5)

    class Meta:
        db_table = "sys_column_privacy"
        verbose_name_plural = "column privacy policies"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(lower_clamp__isnull=True, upper_clamp__isnull=True)
                    | (
                        models.Q(lower_clamp__isnull=False, upper_clamp__isnull=False)
                        & models.Q(lower_clamp__lte=models.F("upper_clamp"))
                    )
                ),
                name="ck_colpriv_clamp_pair_ordered",
            ),
            models.CheckConstraint(condition=models.Q(max_contrib_per_unit__gte=1), name="ck_colpriv_contrib_pos"),
            models.CheckConstraint(condition=models.Q(min_partition_size__gte=0), name="ck_colpriv_min_partition_nonneg"),
        ]
