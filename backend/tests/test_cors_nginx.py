"""P0-6 regression test: verify nginx /api/ proxy and CORS allowed origins for :5173 and :8080."""
from pathlib import Path

from django.conf import settings

ROOT = Path(__file__).resolve().parent.parent.parent


def test_nginx_proxies_api_to_backend_service():
    nginx_conf = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "location /api/" in nginx_conf
    assert "proxy_pass http://api:8000/api/;" in nginx_conf


def test_frontend_api_client_uses_relative_api_base():
    api_ts = (ROOT / "frontend" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "import.meta.env.VITE_API_BASE ?? '/api'" in api_ts


def test_cors_allows_both_vite_and_nginx_ui_origins():
    origins = set(settings.CORS_ALLOWED_ORIGINS)
    assert "http://localhost:5173" in origins
    assert "http://localhost:8080" in origins
