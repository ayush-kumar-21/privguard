"""
Django settings for PrivGuard (BCSE307L course build).

Backend: Django REST Framework + PostgreSQL + Django ORM.
Auth: DRF SimpleJWT (core); Google OAuth is a time-permitting add-on.
Custom user: accounts.Principal (== SYS_PRINCIPAL, the single users table).
"""
from datetime import timedelta
from pathlib import Path

import environ

from .security import check_secrets

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "dev-insecure-change-me"),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173", "http://localhost:8080"]),
)
# Load backend/.env if present (never commit real secrets — see .gitignore).
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    # Local apps (accounts first: it owns AUTH_USER_MODEL)
    "accounts",
    "catalog",
    "budget",
    "audit",
    "safehealth",
    "api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "privguard.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "privguard.wsgi.application"
ASGI_APPLICATION = "privguard.asgi.application"

# --- Database: PostgreSQL via DATABASE_URL (dj-database-url syntax) ----------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://privguard:privguard@localhost:5432/safehealth",
    )
}

# --- Connection pool + session-level guardrails --------------------------------
DATABASES["default"].update({
    "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
    "CONN_HEALTH_CHECKS": True,
    "OPTIONS": {
        "application_name": "privguard-api",
        "options": "-c statement_timeout=15000 -c lock_timeout=3000 "
                   "-c idle_in_transaction_session_timeout=30000",
    },
})
# Deliberately NOT set: ATOMIC_REQUESTS. See PrivGuard_DB_Plan.md §1 rule 5 —
# request-scoped transactions would make the RESERVE phase non-durable and
# collapse the two-phase budget protocol into one phase.

# --- Custom user model (SYS_PRINCIPAL) --------------------------------------
AUTH_USER_MODEL = "accounts.Principal"

# --- Password hashing: Argon2 first (rubric Section 5) ----------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- DRF + JWT --------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "EXCEPTION_HANDLER": "api.exceptions.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# --- CORS (React dev server) ------------------------------------------------
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")

# --- DP noise: server-side secret for deterministic (memoisable) noise seeds ---
PRIVGUARD_SERVER_SECRET = env("PRIVGUARD_SERVER_SECRET", default="dev-noise-secret")

# Every DP query response is padded to at least this long, so the randomised
# running time of the exact noise samplers is not observable (timing channel).
PRIVGUARD_MIN_RESPONSE_MS = env.int("PRIVGUARD_MIN_RESPONSE_MS", default=150)

# Refuse to start in production with a placeholder SECRET_KEY (forgeable JWTs) or noise
# secret (analysts could recompute and subtract their own noise).
check_secrets(DEBUG, SECRET_KEY=SECRET_KEY, PRIVGUARD_SERVER_SECRET=PRIVGUARD_SERVER_SECRET)

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
