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
    class Meta:
        model = Role
        fields = "__all__"


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

    class Meta:
        model = BudgetAccount
        fields = (
            "account_id", "principal_name", "rho_total", "rho_spent",
            "rho_reserved", "rho_remaining", "delta_target", "window_type", "status",
        )


class QueryAuditSerializer(serializers.ModelSerializer):
    principal_name = serializers.CharField(source="principal.username", read_only=True)

    class Meta:
        model = SysQuery
        fields = (
            "query_id", "principal_name", "status", "rho_charged",
            "rows_scanned", "latency_ms", "sql_text", "submitted_at",
        )
