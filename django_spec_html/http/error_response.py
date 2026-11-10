from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import JsonResponse
from django_service_specs import (
    ActionUnavailable,
    AdditionalInputRequired,
    DispatchError,
    InvalidArguments,
    ServiceError,
    ServiceValidationError,
)

from django_spec_html.http.utils import field_map, refusal_status


def error_response(exc: DispatchError | ServiceError) -> JsonResponse:
    """``exc`` as JSON, at the status djangorestframework-services answers it with."""
    status = refusal_status(exc)
    if isinstance(exc, InvalidArguments):
        return JsonResponse(exc.detail, status=status)
    if isinstance(exc, ServiceValidationError):
        return JsonResponse(field_map(exc.detail), status=status)
    if isinstance(exc, ActionUnavailable):
        return JsonResponse({"detail": exc.message, "code": exc.code}, status=status)
    if isinstance(exc, AdditionalInputRequired) and exc.schema is not None:
        return JsonResponse({"detail": exc.message, "schema": _plain(exc.schema)}, status=status)
    return JsonResponse({"detail": exc.message}, status=status)


def _plain(value: Any) -> Any:
    """``value`` with every mapping in it a ``dict``, at any depth."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
