"""Role-based API permissions (Section 5 — role-based API access)."""
from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsDbaOrReadOnly(BasePermission):
    """Any authenticated principal may read; only the `dba` role may write."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_superuser or request.user.has_role("dba")


class IsClinicianOrDbaOrReadOnly(BasePermission):
    """Writes to clinical data are limited to `clinician` / `dba`."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return (
            request.user.is_superuser
            or request.user.has_role("dba")
            or request.user.has_role("clinician")
        )
