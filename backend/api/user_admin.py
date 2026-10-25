"""User administration for the `dba` role: create users, grant roles, credits and run quotas.

    GET    /api/users/        list every principal with roles and budget account
    POST   /api/users/        {username, password, roles[], rho_total?, max_queries?}
    PATCH  /api/users/<id>/   any of {roles[], is_active, password, rho_total, max_queries}

Deliberately not possible here: deleting a user (their ledger and audit rows must stay; deactivate
instead), granting superuser/staff, and leaving the system with no active administrator.
"""
import logging
import math
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.response import Response

from accounts.models import PrincipalRole, Role
from accounts.roles import effective_role_names
from budget.models import BudgetAccount
from catalog.models import SysDatabase

from .permissions import IsDba

Principal = get_user_model()
audit_log = logging.getLogger("privguard.admin")

DB_NAME = "safehealth"
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,64}")  # used with fullmatch: `$` would accept a trailing newline
MAX_RHO = 1000.0
# Roles that run DP queries need a budget account even if the admin gave no figure.
QUERY_ROLES = {"dp_analyst", "auditor"}


class Invalid(Exception):
    def __init__(self, message):
        self.message = message


def _whole(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Invalid(f"{name} must be a whole number >= 0, or null")
    return value


def _rho(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid("rho_total must be a number")
    if not (math.isfinite(value) and 0 < value <= MAX_RHO):
        raise Invalid(f"rho_total must be greater than 0 and at most {MAX_RHO:g}")
    return float(value)


def _roles(names):
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise Invalid("roles must be a non-empty list of role names")
    found = {r.role_name: r for r in Role.objects.filter(role_name__in=names)}
    unknown = sorted(set(names) - set(found))
    if unknown:
        raise Invalid(f"unknown role(s): {', '.join(unknown)}")
    _separation_of_duty(found.values())
    return list(found.values())


def _separation_of_duty(roles):
    """`dba` may read per-patient budgets, so it must never be combined with a role that runs queries."""
    names = {r.role_name for r in roles}
    if "dba" in names and effective_role_names(names) & QUERY_ROLES:
        raise Invalid("an administrator cannot also hold a query role (separation of duty): "
                      "the per-patient ledger shows which patients a query touched")


def _password(raw, user):
    if not isinstance(raw, str) or not raw:
        raise Invalid("password is required")
    try:
        validate_password(raw, user=user)
    except ValidationError as e:
        raise Invalid(" ".join(e.messages))


def _view(user, account=None):
    account = account if account is not None else BudgetAccount.objects.filter(
        principal=user, db__db_name=DB_NAME).first()
    return {
        "id": user.id,
        "username": user.username,
        "kind": user.kind,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "roles": sorted(r.role_name for r in user.roles.all()),
        "account": None if account is None else {
            "account_id": account.account_id,
            "rho_total": account.rho_total,
            "rho_spent": account.rho_spent,
            "rho_remaining": account.rho_remaining,
            "max_queries": account.max_queries,
            "queries_used": account.queries_used,
            "queries_remaining": account.queries_remaining,
        },
    }


def _other_active_admin_exists(excluding):
    """Another active principal who can administer: holds `dba` or is a Django superuser."""
    return Principal.objects.filter(is_active=True).filter(
        Q(roles__role_name="dba") | Q(is_superuser=True)
    ).exclude(pk=excluding.pk).exists()


class UserAdminViewSet(viewsets.ViewSet):
    permission_classes = [IsDba]

    def list(self, request):
        users = Principal.objects.prefetch_related("roles").order_by("id")
        accounts = {
            a.principal_id: a
            for a in BudgetAccount.objects.filter(db__db_name=DB_NAME)
        }
        return Response([_view(u, accounts.get(u.id)) for u in users])

    @transaction.atomic
    def create(self, request):
        body = request.data
        if not hasattr(body, "get"):
            return Response({"error": "expected a JSON object"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            username = body.get("username")
            if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
                raise Invalid("username must be 3-64 characters: letters, digits, . _ -")
            if Principal.objects.filter(username__iexact=username).exists():
                raise Invalid("that username is taken")
            roles = _roles(body.get("roles"))
            user = Principal(username=username, kind="human")
            _password(body.get("password"), user)
            rho = _rho(body["rho_total"]) if body.get("rho_total") is not None else None
            quota = _whole(body.get("max_queries"), "max_queries")
        except Invalid as e:
            return Response({"error": e.message}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(body["password"])  # the configured Argon2 hasher
        try:
            with transaction.atomic():
                user.save()
        except IntegrityError:  # two admins picked the same name at once
            return Response({"error": "that username is taken"}, status=status.HTTP_400_BAD_REQUEST)
        for role in roles:
            PrincipalRole.objects.create(principal=user, role=role)

        names = {r.role_name for r in roles}
        db = SysDatabase.objects.filter(db_name=DB_NAME).first()
        if db and (rho is not None or effective_role_names(names) & QUERY_ROLES):
            BudgetAccount.objects.create(
                principal=user, db=db, delta_target=db.default_delta,
                rho_total=rho if rho is not None else db.default_rho, max_queries=quota,
            )
        audit_log.info("user created: %r by %r roles=%s", user.username, request.user.username, sorted(names))
        return Response(_view(user), status=status.HTTP_201_CREATED)

    @transaction.atomic
    def partial_update(self, request, pk=None):
        user = Principal.objects.select_for_update().filter(pk=pk).first()
        if user is None:
            return Response({"error": "no such user"}, status=status.HTTP_404_NOT_FOUND)
        body, me = request.data, request.user
        if (user.is_superuser or user.is_staff) and not me.is_superuser:
            # Otherwise a dba could reset a superuser's password and become one.
            return Response({"error": "only a superuser can change a superuser or staff account"},
                            status=status.HTTP_403_FORBIDDEN)
        if not hasattr(body, "get"):
            return Response({"error": "expected a JSON object"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            if "is_active" in body:
                if not isinstance(body["is_active"], bool):
                    raise Invalid("is_active must be true or false")
                if not body["is_active"] and user.pk == me.pk:
                    raise Invalid("you cannot deactivate your own account")
            new_roles = _roles(body["roles"]) if "roles" in body else None
            if "password" in body:
                _password(body["password"], user)
            rho = _rho(body["rho_total"]) if body.get("rho_total") is not None else None
            quota_given = "max_queries" in body
            quota = _whole(body.get("max_queries"), "max_queries")

            # Never leave the system without an active administrator.
            was_dba = user.is_superuser or user.roles.filter(role_name="dba").exists()
            will_be_dba = was_dba if new_roles is None else (user.is_superuser or any(r.role_name == "dba" for r in new_roles))
            will_be_active = body.get("is_active", user.is_active)
            if was_dba and not (will_be_dba and will_be_active) and not _other_active_admin_exists(user):
                raise Invalid("this is the last active administrator; make another one first")
            if new_roles is not None and user.pk == me.pk and was_dba and not will_be_dba:
                raise Invalid("you cannot remove your own administrator role")

            account = BudgetAccount.objects.select_for_update().filter(
                principal=user, db__db_name=DB_NAME).first()
            query_role_given = bool(new_roles) and bool(
                effective_role_names(r.role_name for r in new_roles) & QUERY_ROLES)
            if quota_given and quota is not None and account is None and rho is None and not query_role_given:
                raise Invalid("this user has no budget account; give them credits first")
            if rho is not None and account and rho < account.rho_spent + account.rho_reserved:
                raise Invalid(
                    f"credits cannot go below what is already spent or held "
                    f"({account.rho_spent + account.rho_reserved:g})")
        except Invalid as e:
            return Response({"error": e.message}, status=status.HTTP_400_BAD_REQUEST)

        if "is_active" in body:
            user.is_active = body["is_active"]
        if "password" in body:
            user.set_password(body["password"])
        user.save()
        if new_roles is not None:
            keep = {r.pk for r in new_roles}
            PrincipalRole.objects.filter(principal=user).exclude(role_id__in=keep).delete()
            for role in new_roles:
                PrincipalRole.objects.get_or_create(principal=user, role=role)

        db = SysDatabase.objects.filter(db_name=DB_NAME).first()
        if account is None and db and (
            rho is not None or (new_roles and effective_role_names(r.role_name for r in new_roles) & QUERY_ROLES)
        ):
            account = BudgetAccount.objects.create(
                principal=user, db=db, delta_target=db.default_delta,
                rho_total=rho if rho is not None else db.default_rho,
            )
        elif account is not None and rho is not None:
            account.rho_total = rho
        if account is not None:
            if quota_given:
                account.max_queries = quota
            account.save()

        audit_log.info("user updated: %r by %r fields=%s", user.username, me.username,
                       sorted(k for k in body if k != "password") + (["password"] if "password" in body else []))
        user.refresh_from_db()
        return Response(_view(user))
