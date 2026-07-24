from django.contrib import admin

from .models import (
    SysColumn, SysColumnPrivacy, SysDatabase, SysIndex,
    SysIndexColumn, SysTable, SysTablePrivacy,
)


@admin.register(SysTable)
class SysTableAdmin(admin.ModelAdmin):
    list_display = ("table_id", "table_name", "db", "is_system", "est_row_count")
    list_filter = ("db", "is_system")
    search_fields = ("table_name",)


@admin.register(SysColumn)
class SysColumnAdmin(admin.ModelAdmin):
    list_display = ("column_id", "table", "col_name", "data_type", "is_privacy_unit", "is_encrypted")
    list_filter = ("data_type", "is_privacy_unit", "is_encrypted")
    search_fields = ("col_name",)


admin.site.register([
    SysDatabase, SysIndex, SysIndexColumn, SysTablePrivacy, SysColumnPrivacy,
])
