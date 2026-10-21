"""``request_principal`` - the principal an HTTP request carries, as every entry point reads it."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django_service_specs import PrincipalUnavailable, is_deactivated


def request_principal(request: HttpRequest) -> Any:
    """``request.user``, anonymous included, unless it is a deactivated account.

    How ``dispatch_request``, ``adispatch_request`` and both spec views read
    the principal, public so that a host's own view - one rendering a page, or
    reading its arguments some other way - reads it by the same rule, and
    refuses before it reads anything else of the request.

    Anonymous is passed through because a permission check is what decides
    whether anonymous may act, and the spec declares that check; refusing it
    here would make every spec's policy stricter than the one it states.

    A deactivated account is refused, by the rule dispatch itself applies and
    ``resolve_principal`` applies off HTTP: ``ModelBackend`` never logs one in,
    but ``AllowAllUsersModelBackend`` and a project's own backend may. The rule
    asks about authenticated users alone, since ``AnonymousUser.is_active`` is
    ``False``, and a custom user model with no ``is_active`` reads as active.
    Dispatch would refuse the same user; refusing here as well is what keeps
    the request unread for one, as ``adispatch`` resolves a ``principal_id``
    before looking at its arguments.

    Touching ``request.user`` evaluates the lazy object AuthenticationMiddleware
    leaves there, which is a session query, so an async caller calls this off
    the event loop, as ``adispatch_request`` does.

    Raises:
        PrincipalUnavailable: an authenticated user whose ``is_active`` is false.
    """
    user = request.user
    if is_deactivated(user):
        raise PrincipalUnavailable()
    return user
