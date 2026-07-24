from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Attribute, ColumnGrant, KeyMaterial, Principal,
    PrincipalAttribute, PrincipalRole, Role,
)


@admin.register(Principal)
class PrincipalAdmin(UserAdmin):
    list_display = ("username", "kind", "is_active", "is_staff", "date_joined")
    list_filter = ("kind", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (("PrivGuard", {"fields": ("kind",)}),)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("role_id", "role_name", "description")
    search_fields = ("role_name",)


@admin.register(ColumnGrant)
class ColumnGrantAdmin(admin.ModelAdmin):
    list_display = ("grant_id", "column", "role", "access_mode", "rho_multiplier")
    list_filter = ("access_mode", "role")


admin.site.register([Attribute, PrincipalRole, PrincipalAttribute, KeyMaterial])
