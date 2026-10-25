from django.db import connection
from django.db.models import Avg, Count, Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ColumnGrant, Role
from audit.models import SysQuery
from budget.models import BudgetAccount, PatientBudgetView
from catalog.models import SysColumnPrivacy, SysDatabase, SysTablePrivacy
from safehealth.models import (
    Department, Diagnosis, Hospital, LabResult, Patient, Region, Staff, Visit,
)

from privacy.service import run_private_query
from privacy.sql import DPQueryError

from .permissions import IsClinicianOrDbaOrReadOnly, IsDbaOrAuditor, IsDbaOrReadOnly
from . import serializers as s


# --------------------------------------------------------------------------
# SafeHealth application data — full CRUD + search + filter + pagination
# --------------------------------------------------------------------------
class RegionViewSet(viewsets.ModelViewSet):
    queryset = Region.objects.all().order_by("region_id")
    serializer_class = s.RegionSerializer
    permission_classes = [IsDbaOrReadOnly]
    filterset_fields = ["state_code"]
    search_fields = ["region_name", "state_code"]
    ordering_fields = ["region_id", "region_name"]


class HospitalViewSet(viewsets.ModelViewSet):
    queryset = Hospital.objects.select_related("region").order_by("hospital_id")
    serializer_class = s.HospitalSerializer
    permission_classes = [IsDbaOrReadOnly]
    filterset_fields = ["region"]
    search_fields = ["hospital_name"]
    ordering_fields = ["bed_count", "hospital_name"]


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.select_related("hospital").order_by("dept_id")
    serializer_class = s.DepartmentSerializer
    permission_classes = [IsDbaOrReadOnly]
    filterset_fields = ["hospital", "specialty"]
    search_fields = ["dept_name", "specialty"]


class StaffViewSet(viewsets.ModelViewSet):
    queryset = Staff.objects.select_related("dept").order_by("staff_id")
    serializer_class = s.StaffSerializer
    permission_classes = [IsClinicianOrDbaOrReadOnly]
    filterset_fields = ["dept", "staff_role"]
    search_fields = ["full_name", "staff_role"]
    ordering_fields = ["hire_date", "full_name"]


class PatientViewSet(viewsets.ModelViewSet):
    queryset = Patient.objects.select_related("region").order_by("patient_id")
    serializer_class = s.PatientSerializer
    permission_classes = [IsClinicianOrDbaOrReadOnly]
    filterset_fields = ["region", "sex", "insurance_type"]
    search_fields = ["zip3", "insurance_type"]
    ordering_fields = ["birth_year", "patient_id"]


class VisitViewSet(viewsets.ModelViewSet):
    queryset = Visit.objects.select_related("patient", "dept", "attending_staff").order_by("visit_id")
    serializer_class = s.VisitSerializer
    permission_classes = [IsClinicianOrDbaOrReadOnly]
    filterset_fields = ["dept", "admission_type", "patient"]
    search_fields = ["admission_type"]
    ordering_fields = ["admit_ts", "total_charge", "visit_id"]


class DiagnosisViewSet(viewsets.ModelViewSet):
    queryset = Diagnosis.objects.select_related("visit").order_by("diagnosis_id")
    serializer_class = s.DiagnosisSerializer
    permission_classes = [IsClinicianOrDbaOrReadOnly]
    filterset_fields = ["visit", "is_primary", "icd10_code"]
    search_fields = ["icd10_code"]


class LabResultViewSet(viewsets.ModelViewSet):
    queryset = LabResult.objects.select_related("visit").order_by("lab_id")
    serializer_class = s.LabResultSerializer
    permission_classes = [IsClinicianOrDbaOrReadOnly]
    filterset_fields = ["visit", "test_code"]
    search_fields = ["test_code"]


# --------------------------------------------------------------------------
# Policy / access-control admin — dba-gated writes
# --------------------------------------------------------------------------
class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.all().order_by("role_id")
    serializer_class = s.RoleSerializer
    permission_classes = [IsDbaOrReadOnly]
    search_fields = ["role_name"]


class ColumnGrantViewSet(viewsets.ModelViewSet):
    queryset = ColumnGrant.objects.select_related("column", "role").order_by("grant_id")
    serializer_class = s.ColumnGrantSerializer
    permission_classes = [IsDbaOrReadOnly]
    filterset_fields = ["role", "access_mode"]


class ColumnPrivacyViewSet(viewsets.ModelViewSet):
    queryset = SysColumnPrivacy.objects.select_related("column").order_by("policy_id")
    serializer_class = s.ColumnPrivacySerializer
    permission_classes = [IsDbaOrReadOnly]
    filterset_fields = ["mechanism", "is_groupable"]


class TablePrivacyViewSet(viewsets.ModelViewSet):
    queryset = SysTablePrivacy.objects.select_related("table").order_by("table_id")
    serializer_class = s.TablePrivacySerializer
    permission_classes = [IsDbaOrReadOnly]


# --------------------------------------------------------------------------
# Read-only reporting — scoped to the caller unless dba/auditor
# --------------------------------------------------------------------------
def _scoped(qs, user):
    if user.is_superuser or user.has_role("dba") or user.has_role("auditor"):
        return qs
    return qs.filter(principal=user)


class BudgetAccountViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = s.BudgetAccountSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return _scoped(BudgetAccount.objects.select_related("principal"), self.request.user).order_by("account_id")

    @action(detail=True, methods=["post"], permission_classes=[IsDbaOrReadOnly])
    def quota(self, request, pk=None):
        """POST /api/budget-accounts/<id>/quota/  {"max_queries": 25|null, "reset_used": false}

        DBA only. Takes effect on the account's very next query."""
        body = request.data
        if "max_queries" not in body:
            return Response({"error": "max_queries is required (a whole number, or null)"}, status=400)
        limit = body["max_queries"]
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
            return Response({"error": "max_queries must be a whole number >= 0, or null"}, status=400)
        fields = {"max_queries": limit}
        if body.get("reset_used") is True:
            fields["queries_used"] = 0
        updated = self.get_queryset().filter(pk=pk).update(**fields)
        if not updated:
            return Response({"error": "no such budget account"}, status=404)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)


class QueryAuditViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = s.QueryAuditSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status"]
    search_fields = ["sql_text"]

    @property
    def ordering_fields(self):
        # Sorting by a hidden column would leak its order, so analysts may not
        # sort by latency (see QueryAuditSerializer).
        fields = ["submitted_at", "rho_charged"]
        user = getattr(getattr(self, "request", None), "user", None)
        if user and (user.is_superuser or user.has_role("dba") or user.has_role("auditor")):
            fields.append("latency_ms")
        return fields

    def get_queryset(self):
        return _scoped(SysQuery.objects.select_related("principal"), self.request.user).order_by("-submitted_at")


# --------------------------------------------------------------------------
# Differential-privacy query gateway
# --------------------------------------------------------------------------
class DPQueryView(APIView):
    """POST /api/dp/query  { "db": "safehealth", "sql": "...", "rho": 0.05 }"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        db_name = request.data.get("db", "safehealth")
        sql = request.data.get("sql")
        if not sql:
            return Response({"error": "sql is required"}, status=status.HTTP_400_BAD_REQUEST)
        rho = float(request.data.get("rho", 0.05))
        try:
            result = run_private_query(request.user, db_name, sql, rho_req=rho)
        except DPQueryError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class BudgetStatusView(APIView):
    """GET /api/dp/budget?db=safehealth — the caller's budget account."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        db_name = request.query_params.get("db", "safehealth")
        db = SysDatabase.objects.filter(db_name=db_name).first()
        account = BudgetAccount.objects.filter(principal=request.user, db=db).first() if db else None
        if not account:
            return Response({"error": "no budget account"}, status=status.HTTP_404_NOT_FOUND)
        return Response(s.BudgetAccountSerializer(account).data)


class LedgerVerifyView(APIView):
    """GET /api/dp/ledger/verify?db=safehealth — replays the caller's hash chain."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        db_name = request.query_params.get("db", "safehealth")
        db = SysDatabase.objects.filter(db_name=db_name).first()
        account = BudgetAccount.objects.filter(principal=request.user, db=db).first() if db else None
        if not account:
            return Response({"error": "no budget account"}, status=status.HTTP_404_NOT_FOUND)
        with connection.cursor() as cur:
            cur.execute("SELECT ok, entries, first_bad_entry FROM fn_verify_ledger(%s)", [account.account_id])
            ok, entries, first_bad = cur.fetchone()
        return Response({"ok": ok, "entries": entries, "first_bad_entry": first_bad})


class PatientBudgetViewSet(viewsets.ReadOnlyModelViewSet):
    """Per-patient privacy budgets (auditor or dba). Spending happens only inside queries."""
    serializer_class = s.PatientBudgetSerializer
    permission_classes = [IsDbaOrAuditor]
    queryset = PatientBudgetView.objects.all().order_by("-rho_spent", "patient_id")
    filterset_fields = ["status"]
    search_fields = ["patient_id"]
    ordering_fields = ["patient_id", "rho_spent", "rho_remaining", "charges"]

    @action(detail=False, methods=["get"])
    def summary(self, request):
        agg = PatientBudgetView.objects.aggregate(
            patients=Count("patient_id"), spent=Sum("rho_spent"), total=Sum("rho_total"),
            charges=Sum("charges"), mean_remaining=Avg("rho_remaining"))
        by_status = {r["status"]: r["n"] for r in
                     PatientBudgetView.objects.values("status").annotate(n=Count("patient_id"))}
        return Response({
            "patients": agg["patients"] or 0,
            "active": by_status.get("active", 0),
            "exhausted": by_status.get("exhausted", 0),
            "erased": by_status.get("erased", 0),
            "charges": agg["charges"] or 0,
            "rho_spent": agg["spent"] or 0.0,
            "rho_total": agg["total"] or 0.0,
            "mean_remaining": agg["mean_remaining"] or 0.0,
        })

    @action(detail=True, methods=["get"])
    def verify(self, request, pk=None):
        """Replay this patient's hash chain."""
        if not PatientBudgetView.objects.filter(pk=pk).exists():
            return Response({"error": "no such patient"}, status=status.HTTP_404_NOT_FOUND)
        with connection.cursor() as cur:
            cur.execute("SELECT ok, entries, first_bad_entry FROM fn_verify_patient_chain(%s)", [int(pk)])
            ok, entries, first_bad = cur.fetchone()
        return Response({"patient_id": int(pk), "ok": ok, "entries": entries, "first_bad_entry": first_bad})
