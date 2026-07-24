from django.contrib import admin

from .models import SysNoiseCache, SysPredicateOverlap, SysQuery, SysQueryFingerprint


@admin.register(SysQuery)
class SysQueryAdmin(admin.ModelAdmin):
    list_display = ("query_id", "principal", "status", "rho_charged", "latency_ms", "submitted_at")
    list_filter = ("status",)
    search_fields = ("sql_text",)


@admin.register(SysQueryFingerprint)
class SysQueryFingerprintAdmin(admin.ModelAdmin):
    list_display = ("fingerprint_id", "table", "agg_kind", "times_seen", "first_seen")
    list_filter = ("agg_kind",)


admin.site.register([SysNoiseCache, SysPredicateOverlap])
