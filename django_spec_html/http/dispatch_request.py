"""``dispatch_request`` - one request, dispatched and answered, from a hand-written view."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse

from django_service_specs.authorization.grant import Grant
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.present import present
from django_service_specs.http.error_response import error_response
from django_service_specs.http.request_arguments import request_arguments
from django_service_specs.http.utils import (
    not_found_response,
    request_principal,
    success_response,
)
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from django_service_specs.services.service_error import ServiceError
from django_service_specs.specs.selector_spec import SelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.types.dispatch_error import DispatchError
from django_service_specs.validation.unknown_arguments import UnknownArguments


def dispatch_request(
    spec: ServiceSpec | SelectorSpec,
    request: HttpRequest,
    *,
    url_kwargs: Mapping[str, Any] | None = None,
    success_status: int | None = None,
    grant: Grant | None = None,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
) -> HttpResponse:
    """Dispatch ``spec`` for ``request`` and answer it as JSON: the function a view calls.

    1. **The principal is** ``request.user``, anonymous included, since the
       spec's permission check is what decides whether anonymous may act. An
       authenticated user whose ``is_active`` is false is refused as
       ``PrincipalUnavailable``: a backend other than ``ModelBackend`` can log
       one in, and a deactivated principal never acts.
    2. **The arguments** are
       [`request_arguments`][django_service_specs.http.request_arguments.request_arguments]
       for ``spec.parameters()``, with ``url_kwargs`` merged last.
    3. [`dispatch`][django_service_specs.dispatch.dispatch.dispatch], with
       ``grant``, ``pool_seeds`` and ``unknown_arguments`` as given.
    4. **The answer.** A not-found result is 404 with a ``detail``. Otherwise
       the presented value, bare, at ``success_status`` if given, else 204 for
       a service with nothing to present and 200 for everything else - the
       defaults djangorestframework-services answers with. A 204 has no body.

    A refusal of either family, from any of the four steps, is answered by
    [`error_response`][django_service_specs.http.error_response.error_response].
    Anything else propagates, a configuration error included: it is wrong for
    every caller, so it belongs to the host's error handling rather than to one
    client's response. A URL kwarg ``spec`` does not declare is one, raised
    as ``ImproperlyConfigured``: every URL kwarg is an argument, and the route
    is the host's.

    **CSRF is the host's middleware**, as for any Django view: nothing here
    exempts a view, and ``csrf_exempt`` is one line for a host that wants it.
    """
    try:
        # The principal first, as ``adispatch`` resolves a ``principal_id``
        # before its shape check: a deactivated account is refused without
        # learning anything about its request.
        principal = request_principal(request)
        arguments = request_arguments(request, spec.parameters(), url_kwargs=url_kwargs)
        result = dispatch(
            spec,
            principal=principal,
            arguments=arguments,
            grant=grant,
            pool_seeds=pool_seeds,
            unknown_arguments=unknown_arguments,
        )
        if result.kind == "not_found":
            return not_found_response()
        return success_response(spec, present(spec, result), success_status)
    except (DispatchError, ServiceError) as refused:
        return error_response(refused)
