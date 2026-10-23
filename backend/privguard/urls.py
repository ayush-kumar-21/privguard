"""PrivGuard URL configuration."""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from rest_framework_simplejwt.views import (
    TokenObtainPairView, TokenRefreshView, TokenBlacklistView,
)

from accounts.views import MeView, RegisterView


def health(_request):
    return JsonResponse({"status": "ok", "service": "privguard-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    # --- Auth (SimpleJWT) ---
    path("api/auth/register/", RegisterView.as_view(), name="register"),
    path("api/auth/login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/logout/", TokenBlacklistView.as_view(), name="token_blacklist"),
    path("api/auth/me/", MeView.as_view(), name="me"),
    # --- REST API + DP gateway ---
    path("api/", include("api.urls")),
    path("monitor/", include("monitor.urls")),
]
