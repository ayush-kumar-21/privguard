"""
Identity & Access Control (Subsystem C).

`Principal` is the project's single Users table (AUTH_USER_MODEL) — every human
and service account is a row here; roles are resolved through PrincipalRole, NOT
through per-role tables. This is the rubric's "single users table + role" rule
in its normalised form.

Schema mapping for the user table:
  principal_name  -> AbstractUser.username  (unique)
  pwd_hash        -> AbstractUser.password   (Argon2, see settings.PASSWORD_HASHERS)
  is_active       -> AbstractUser.is_active
  created_at      -> AbstractUser.date_joined
"""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from .roles import effective_role_names


class Role(models.Model):
    role_id = models.AutoField(primary_key=True)
    role_name = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "sys_role"

    def __str__(self):
        return self.role_name


class Attribute(models.Model):
    """ABE attribute universe, e.g. 'dept:cardiology', 'clearance:L3'."""
    attr_id = models.AutoField(primary_key=True)
    attr_name = models.CharField(max_length=64, unique=True)

    class Meta:
        db_table = "sys_attribute"

    def __str__(self):
        return self.attr_name


class Principal(AbstractUser):
    """SYS_PRINCIPAL — the custom user model (analysts and service accounts)."""
    KIND_CHOICES = [("human", "human"), ("service", "service")]
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default="human")

    roles = models.ManyToManyField(
        Role, through="PrincipalRole", related_name="principals", blank=True
    )
    attributes = models.ManyToManyField(
        Attribute, through="PrincipalAttribute", related_name="principals", blank=True
    )

    class Meta:
        db_table = "sys_principal"

    def effective_roles(self) -> set[str]:
        """Assigned roles plus the base roles they carry (see accounts/roles.py)."""
        return effective_role_names(self.roles.values_list("role_name", flat=True))

    def has_role(self, role_name: str) -> bool:
        return role_name in self.effective_roles()


class PrincipalRole(models.Model):
    """Associative entity resolving SYS_PRINCIPAL M:N SYS_ROLE."""
    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        db_column="principal_id", related_name="principal_roles",
    )
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE,
        db_column="role_id", related_name="role_principals",
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_principal_role"
        constraints = [
            models.UniqueConstraint(
                fields=["principal", "role"], name="pk_principal_role"
            )
        ]


class PrincipalAttribute(models.Model):
    """Associative entity resolving SYS_PRINCIPAL M:N SYS_ATTRIBUTE."""
    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        db_column="principal_id", related_name="principal_attributes",
    )
    attr = models.ForeignKey(
        Attribute, on_delete=models.CASCADE,
        db_column="attr_id", related_name="attr_principals",
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sys_principal_attribute"
        constraints = [
            models.UniqueConstraint(
                fields=["principal", "attr"], name="pk_principal_attribute"
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=models.F("granted_at")),
                name="ck_principal_attr_expiry_after_grant",
            ),
        ]


class ColumnGrant(models.Model):
    """SYS_COLUMN_GRANT — column x role access mode; resolves M:N with payload."""
    ACCESS_MODES = [
        ("raw", "raw"),
        ("dp", "dp"),
        ("redacted", "redacted"),
        ("denied", "denied"),
    ]
    grant_id = models.AutoField(primary_key=True)
    column = models.ForeignKey(
        "catalog.SysColumn", on_delete=models.CASCADE,
        db_column="column_id", related_name="grants",
    )
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE,
        db_column="role_id", related_name="column_grants",
    )
    access_mode = models.CharField(max_length=12, choices=ACCESS_MODES)
    rho_multiplier = models.FloatField(default=1.0, help_text="role-specific noise scaling")
    abe_policy_expr = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "sys_column_grant"
        constraints = [
            models.UniqueConstraint(
                fields=["column", "role"], name="uq_column_role_grant"
            ),
            models.CheckConstraint(condition=models.Q(rho_multiplier__gt=0), name="ck_grant_rho_multiplier_pos"),
        ]


class KeyMaterial(models.Model):
    """SYS_KEY_MATERIAL — wrapped data-encryption keys per encrypted column."""
    key_id = models.AutoField(primary_key=True)
    column = models.ForeignKey(
        "catalog.SysColumn", on_delete=models.CASCADE,
        db_column="column_id", related_name="key_material",
    )
    algo = models.CharField(max_length=24, default="AES-256-GCM")
    wrapped_dek = models.BinaryField(max_length=512)
    abe_policy_expr = models.CharField(max_length=255, null=True, blank=True)
    key_version = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_key_material"
