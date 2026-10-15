"""Uniform JSON error envelope for the API (Section 4 — exception handling).

Every error leaves the API as ``{"error": {"code", "message", "details"}}`` so the
React client has one shape to parse, whether DRF raised it or the database did.
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def _envelope(code: str, message: str, details=None, http_status=400) -> Response:
    return Response(
        {"error": {"code": code, "message": message, "details": details}},
        status=http_status,
    )


def api_exception_handler(exc, context):
    # Constraint violations (CHECK / UNIQUE / FK) are client errors, not 500s.
    if isinstance(exc, IntegrityError):
        logger.warning("integrity error: %s", exc)
        return _envelope(
            "integrity_error",
            "The change violates a database constraint.",
            http_status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, DjangoValidationError):
        return _envelope("validation_error", "; ".join(exc.messages), exc.messages)
    if isinstance(exc, DatabaseError):
        logger.exception("database error")
        return _envelope(
            "database_error",
            "The database could not complete the request.",
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # unhandled -> Django's 500 handling (logged, no stack leak)

    data = response.data
    message = data.get("detail") if isinstance(data, dict) else None
    # DRF has already normalised Http404/PermissionDenied into ErrorDetail,
    # which carries the machine-readable code (e.g. "not_found").
    code = getattr(message, "code", None) or "error"
    if message is None:
        message = "The request was invalid."
        code = "validation_error"
    response.data = {
        "error": {
            "code": str(code),
            "message": str(message),
            # Field-level errors are kept so forms can show them inline.
            "details": None if (isinstance(data, dict) and "detail" in data) else data,
        }
    }
    return response
