"""The gateway application.

Paths, trailing slashes, pagination envelope and error envelope mirror the legacy Django API
(`backend/api`), so the existing React client talks to either without changes. Everything
that touches data is behind the [`EngineClient`] seam.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .engine import AuditRow, EngineClient, EngineError, StubEngine
from .security import (
    DUMMY_HASH,
    TokenError,
    decode_token,
    encode_token,
    new_jti,
    verify_password,
)
from .settings import Settings
from .store import Principal, RefreshRegistry, UserStore

PAGE_SIZE = 20
DEFAULT_DB = "safehealth"
MAX_SQL_LENGTH = 2000
MAX_RHO = 5.0


def _envelope(code: str, message: str, status: int, details=None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details}}, status_code=status
    )


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=1024)


class RefreshBody(BaseModel):
    refresh: str = Field(min_length=1, max_length=4096)


class QueryBody(BaseModel):
    db: str = DEFAULT_DB
    sql: str = Field(min_length=1, max_length=MAX_SQL_LENGTH)
    rho: float = Field(default=0.05, gt=0, le=MAX_RHO, allow_inf_nan=False)


def create_app(
    settings: Settings | None = None,
    engine: EngineClient | None = None,
    users: UserStore | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = engine or StubEngine()
    if users is None:
        users = (
            UserStore.with_demo_principals(settings.demo_password)
            if settings.demo_users
            else UserStore()
        )
    refresh_registry = RefreshRegistry()

    app = FastAPI(title="PrivGuard gateway", docs_url=None, redoc_url=None, openapi_url=None)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # ------------------------------------------------------------ errors
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = {401: "not_authenticated", 403: "permission_denied", 404: "not_found"}.get(
            exc.status_code, "error"
        )
        return _envelope(code, str(exc.detail), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return _envelope("validation_error", "The request was invalid.", 400, details)

    @app.exception_handler(EngineError)
    async def engine_error(_: Request, exc: EngineError):
        return _envelope(exc.sqlstate, exc.message, exc.http_status)

    # -------------------------------------------------------------- auth
    bearer = HTTPBearer(auto_error=False)

    def current_principal(
        creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> Principal:
        if creds is None:
            raise HTTPException(401, "Authentication credentials were not provided.")
        try:
            claims = decode_token(settings.jwt_secret, creds.credentials, "access")
        except TokenError:
            raise HTTPException(401, "Given token not valid for any token type.") from None
        principal = users.by_id(int(claims.subject)) if claims.subject.isdigit() else None
        if principal is None or not principal.is_active:
            raise HTTPException(401, "User not found.")
        return principal

    def issue_pair(principal: Principal) -> dict:
        jti = new_jti()
        refresh_registry.register(jti)
        return {
            "access": encode_token(
                settings.jwt_secret, str(principal.id), "access", settings.access_ttl_seconds
            ),
            "refresh": encode_token(
                settings.jwt_secret, str(principal.id), "refresh", settings.refresh_ttl_seconds, jti
            ),
        }

    @app.post("/api/auth/login/")
    def login(body: LoginBody):
        principal = users.by_username(body.username)
        # Verify even for an unknown name so the response time does not reveal which exist.
        ok = verify_password(principal.password_hash if principal else DUMMY_HASH, body.password)
        if not (ok and principal and principal.is_active):
            raise HTTPException(401, "No active account found with the given credentials.")
        return issue_pair(principal)

    def redeem_refresh(token: str) -> Principal:
        try:
            claims = decode_token(settings.jwt_secret, token, "refresh")
        except TokenError:
            raise HTTPException(401, "Token is invalid or expired.") from None
        if not refresh_registry.redeem(claims.jti):
            raise HTTPException(401, "Token is blacklisted.")
        principal = users.by_id(int(claims.subject)) if claims.subject.isdigit() else None
        if principal is None or not principal.is_active:
            raise HTTPException(401, "User not found.")
        return principal

    @app.post("/api/auth/refresh/")
    def refresh(body: RefreshBody):
        return issue_pair(redeem_refresh(body.refresh))

    @app.post("/api/auth/logout/")
    def logout(body: RefreshBody):
        # Idempotent: a token that is already spent or invalid is simply not usable any more.
        try:
            claims = decode_token(settings.jwt_secret, body.refresh, "refresh")
            refresh_registry.redeem(claims.jti)
        except TokenError:
            pass
        return {}

    @app.get("/api/auth/me/")
    def me(principal: Principal = Depends(current_principal)):
        return principal.public()

    # --------------------------------------------------------- dp surface
    def require_db(db: str) -> None:
        if db != DEFAULT_DB:
            raise EngineError("catalog", f"unknown database: {db}")

    @app.post("/api/dp/query/")
    def dp_query(body: QueryBody, principal: Principal = Depends(current_principal)):
        require_db(body.db)
        out = engine.query(principal, body.sql, body.rho)
        return {
            "status": out.status,
            "result": out.result,
            "rho_charged": out.rho_charged,
            "epsilon_charged": out.epsilon_charged,
            "mechanism": out.mechanism,
            "query_id": out.query_id,
            "suppressed_groups": out.suppressed_groups,
            "budget": out.budget,
        }

    @app.get("/api/dp/budget/")
    def dp_budget(db: str = DEFAULT_DB, principal: Principal = Depends(current_principal)):
        require_db(db)
        view = engine.budget(principal)
        if view is None:
            raise HTTPException(404, "no budget account")
        return view.__dict__

    @app.get("/api/dp/ledger/verify/")
    def dp_ledger_verify(db: str = DEFAULT_DB, principal: Principal = Depends(current_principal)):
        require_db(db)
        verdict = engine.verify_ledger(principal)
        if verdict is None:
            raise HTTPException(404, "no budget account")
        return verdict

    # ---------------------------------------------------- paginated views
    def paginate(request: Request, rows: list[dict], page: int) -> dict:
        start = (page - 1) * PAGE_SIZE
        has_next = start + PAGE_SIZE < len(rows)
        has_prev = page > 1
        return {
            "count": len(rows),
            "next": str(request.url.include_query_params(page=page + 1)) if has_next else None,
            "previous": str(request.url.include_query_params(page=page - 1)) if has_prev else None,
            "results": rows[start : start + PAGE_SIZE],
        }

    @app.get("/api/budget-accounts/")
    def budget_accounts(
        request: Request,
        page: int = Query(1, ge=1),
        principal: Principal = Depends(current_principal),
    ):
        return paginate(request, [v.__dict__ for v in engine.budgets(principal)], page)

    sortable = {"submitted_at", "rho_charged", "query_id"}

    @app.get("/api/queries/")
    def queries(
        request: Request,
        page: int = Query(1, ge=1),
        status: str | None = None,
        search: str | None = None,
        ordering: str = "-submitted_at",
        principal: Principal = Depends(current_principal),
    ):
        field = ordering.lstrip("-")
        # Sorting by latency would leak its order to analysts, who may not see the value.
        if field not in sortable | ({"latency_ms"} if principal.is_privileged else set()):
            raise HTTPException(400, f"cannot order by {field}")
        rows: list[AuditRow] = engine.audit(principal)
        if status:
            rows = [r for r in rows if r.status == status]
        if search:
            rows = [r for r in rows if search.lower() in r.sql_text.lower()]
        rows = sorted(rows, key=lambda r: getattr(r, field), reverse=ordering.startswith("-"))
        out = []
        for r in rows:
            d = dict(r.__dict__)
            # Server-side latency is data-dependent: a timing side channel for analysts.
            if not principal.is_privileged:
                d.pop("latency_ms")
            out.append(d)
        return paginate(request, out, page)

    @app.get("/api/healthz")
    def healthz():
        return {"ok": True}

    return app
