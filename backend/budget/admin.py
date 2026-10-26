from django.contrib import admin

from .models import BudgetAccount, BudgetLedger, PatientBudget, PatientLedger


@admin.register(BudgetAccount)
class BudgetAccountAdmin(admin.ModelAdmin):
    list_display = (
        "account_id", "principal", "db", "rho_total", "rho_spent", "rho_reserved", "status",
        "max_queries", "queries_used",
    )
    # Edit a quota straight from the list; it applies to the account's next query.
    list_editable = ("max_queries",)
    list_filter = ("status", "window_type")


@admin.register(BudgetLedger)
class BudgetLedgerAdmin(admin.ModelAdmin):
    list_display = ("entry_id", "account", "op", "rho_delta", "rho_after", "logged_at")
    list_filter = ("op",)
    # Ledger is append-only; block edits/deletes in the admin too.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PatientBudget)
class PatientBudgetAdmin(admin.ModelAdmin):
    list_display = ("patient", "rho_total", "rho_spent", "status")
    list_filter = ("status",)
    search_fields = ("patient__patient_id",)
    readonly_fields = ("rho_spent", "status")  # spending moves only through fn_charge/refund_patients


@admin.register(PatientLedger)
class PatientLedgerAdmin(admin.ModelAdmin):
    list_display = ("entry_id", "patient", "query", "op", "rho_delta", "rho_after", "logged_at")
    list_filter = ("op",)

    def has_change_permission(self, request, obj=None):
        return False  # append-only

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
