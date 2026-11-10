from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django_service_specs import PrincipalUnavailable, is_deactivated


def request_principal(request: HttpRequest) -> Any:
    """``request.user``, anonymous included, unless it is a deactivated account."""
    user = request.user
    if is_deactivated(user):
        raise PrincipalUnavailable()
    return user
