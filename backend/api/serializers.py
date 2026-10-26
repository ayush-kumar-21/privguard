from django.db.models import Sum
from rest_framework import serializers

from accounts.models import ColumnGrant, Role
from audit.models import SysQuery
from budget.models import BudgetAccount
from catalog.models import SysColumnPrivacy, SysTablePrivacy
from safehealth.models import (
    Department, Diagnosis, Hospital, LabResult, Patient, Region, Staff, Visit,
)


# --- SafeHealth application data (CRUD) ---
class RegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = "__all__"


class HospitalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hospital
        fields = "__all__"


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = "__all__"


class StaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = Staff
        fields = "__all__"


class PatientSerializer(serializers.ModelSerializer):
    # `mrn` is access-controlled (denied for analysts) -> never serialised here.
    age = serializers.IntegerField(read_only=True)

    class Meta:
        model = Patient
        fields = (
            "patient_id", "region", "birth_year", "age", "sex",
            "zip3", "insurance_type", "enrolled_at",
        )


class VisitSerializer(serializers.ModelSerializer):
    length_of_stay_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = Visit
        fields = (
            "visit_id", "patient", "dept", "attending_staff", "admit_ts",
            "discharge_ts", "length_of_stay_days", "total_charge", "admission_type",
        )


class DiagnosisSerializer(serializers.ModelSerializer):
    class Meta:
        model = Diagnosis
        fields = "__all__"


class LabResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabResult
        fields = "__all__"


# --- Policy / access-control admin (dba-gated writes) ---
class RoleSerializer(serializers.ModelSerializer):
    # The base role a job role carries (null for the base roles themselves).
    carries = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ("role_id", "role_name", "description", "carries")

    def get_carries(self, obj) -> str | None:
        from accounts.roles import carried_role
        return carried_role(obj.role_name)


class ColumnGrantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ColumnGrant
        fields = "__all__"


class ColumnPrivacySerializer(serializers.ModelSerializer):
    class Meta:
        model = SysColumnPrivacy
        fields = "__all__"


class TablePrivacySerializer(serializers.ModelSerializer):
    class Meta:
        model = SysTablePrivacy
        fields = "__all__"


# --- Read-only reporting ---
class BudgetAccountSerializer(serializers.ModelSerializer):
    principal_name = serializers.CharField(source="principal.username", read_only=True)
    rho_remaining = serializers.FloatField(read_only=True)
    queries_remaining = serializers.IntegerField(read_only=True)
    delta_spent = serializers.SerializerMethodField()

    class Meta:
        model = BudgetAccount
        fields = (
            "account_id", "principal_name", "rho_total", "rho_spent",
            "rho_reserved", "rho_remaining", "delta_target", "delta_spent",
            "window_type", "status", "max_queries", "queries_used", "queries_remaining",
        )

    def get_delta_spent(self, obj) -> float:
        """delta spent outside the rho ledger (DP partition selection)."""
        return SysQuery.objects.filter(
            principal=obj.principal, db=obj.db, status="executed",
        ).aggregate(total=Sum("delta_charged"))["total"] or 0.0


class QueryAuditSerializer(serializers.ModelSerializer):
    """Audit view of a query.

    Deliberately omits `rows_scanned`: for a COUNT it is the *exact* un-noised
    answer, so exposing it to the analyst who ran the query would bypass the DP
    mechanism entirely. `latency_ms` is data-dependent and therefore a timing
    side channel (Haeberlen et al., Jin et al.), so it is shown only to roles
    that already see the whole audit log.
    """
    principal_name = serializers.CharField(source="principal.username", read_only=True)
    delta_charged = serializers.FloatField(read_only=True)

    class Meta:
        model = SysQuery
        fields = (
            "query_id", "principal_name", "status", "rho_charged", "delta_charged",
            "latency_ms", "sql_text", "submitted_at",
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        user = getattr(self.context.get("request"), "user", None)
        if not (user and (user.is_superuser or user.has_role("dba") or user.has_role("auditor"))):
            data.pop("latency_ms", None)
        return data


class PatientBudgetSerializer(serializers.Serializer):
    """One patient's budget (from the v_patient_budget view). Patient ids only: no identifiers."""
    patient_id = serializers.IntegerField()
    rho_total = serializers.FloatField()
    rho_spent = serializers.FloatField()
    rho_remaining = serializers.FloatField()
    status = serializers.CharField()
    charges = serializers.IntegerField()
    last_charged_at = serializers.DateTimeField(allow_null=True)
