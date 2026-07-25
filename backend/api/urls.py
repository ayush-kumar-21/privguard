from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
# SafeHealth application data (CRUD)
router.register("regions", views.RegionViewSet)
router.register("hospitals", views.HospitalViewSet)
router.register("departments", views.DepartmentViewSet)
router.register("staff", views.StaffViewSet)
router.register("patients", views.PatientViewSet)
router.register("visits", views.VisitViewSet)
router.register("diagnoses", views.DiagnosisViewSet)
router.register("lab-results", views.LabResultViewSet)
# Policy / access-control admin
router.register("roles", views.RoleViewSet)
router.register("column-grants", views.ColumnGrantViewSet)
router.register("column-policies", views.ColumnPrivacyViewSet)
router.register("table-policies", views.TablePrivacyViewSet)
# Read-only reporting
router.register("budget-accounts", views.BudgetAccountViewSet, basename="budget-account")
router.register("queries", views.QueryAuditViewSet, basename="query")

urlpatterns = [
    path("", include(router.urls)),
    # Differential-privacy gateway
    path("dp/query/", views.DPQueryView.as_view(), name="dp-query"),
    path("dp/budget/", views.BudgetStatusView.as_view(), name="dp-budget"),
    path("dp/ledger/verify/", views.LedgerVerifyView.as_view(), name="dp-ledger-verify"),
]
