from django.contrib import admin

from .models import BudgetAccount, BudgetLedger


@admin.register(BudgetAccount)
class BudgetAccountAdmin(admin.ModelAdmin):
    list_display = ("account_id", "principal", "db", "rho_total", "rho_spent", "rho_reserved", "status")
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
