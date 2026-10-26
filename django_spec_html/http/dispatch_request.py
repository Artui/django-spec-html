"""``dispatch_request`` - one request, dispatched and answered, from a hand-written view."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DispatchError,
    Grant,
    PoolSeeds,
    SelectorSpec,
    ServerArguments,
    ServiceError,
    ServiceSpec,
    SpecSource,
    UnknownArguments,
    apply_server_arguments,
    dispatch,
    kernel_spec,
    present,
)

from django_spec_html.http.error_response import error_response
from django_spec_html.http.request_arguments import request_arguments
from django_spec_html.http.request_principal import request_principal
from django_spec_html.http.utils import (
    check_asked_input,
    not_found_response,
    success_response,
)


def dispatch_request(
    spec: ServiceSpec | SelectorSpec | SpecSource,
    request: HttpRequest,
    *,
    url_kwargs: Mapping[str, Any] | None = None,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None,
    server_arguments: ServerArguments | None = None,
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
       one in, and a deactivated principal never acts. Dispatch refuses one by
       the same rule; refusing here as well keeps the request unread for it.
    2. **The arguments** are
       [`request_arguments`][django_spec_html.http.request_arguments.request_arguments]
       for ``spec.parameters()``, with ``url_kwargs`` merged last under the
       names ``route_kwargs`` maps them to, and ``server_arguments`` applied
       over them by the kernel's
       [`apply_server_arguments`][django_service_specs.dispatch.apply_server_arguments.apply_server_arguments]:
       its bound values outrank whatever the client sent, and its withheld
       names are dropped. A URL kwarg filling one of its names is refused,
       as the host's misconfiguration. The binding is not checked here:
       a host's own view calls
       [`check_server_arguments`][django_service_specs.dispatch.check_server_arguments.check_server_arguments]
       once, when it is built, as ``SpecView.as_view()`` does.
    3. [`dispatch`][django_service_specs.dispatch.dispatch.dispatch], with
       ``grant``, ``pool_seeds`` and ``unknown_arguments`` as given.
    4. **The answer.** A not-found result is 404 with a ``detail``. Otherwise
       the presented value, bare, at ``success_status`` if given, else 204 for
       a service with nothing to present and 200 for everything else - the
       defaults djangorestframework-services answers with. A 204 has no body.

    A refusal of either family, from any of the four steps, is answered by
    [`error_response`][django_spec_html.http.error_response.error_response].
    Anything else propagates, a configuration error included: it is wrong for
    every caller, so it belongs to the host's error handling rather than to one
    client's response. A URL kwarg ``spec`` does not declare, and that
    ``route_kwargs`` neither renames nor drops, is one, raised as
    ``ImproperlyConfigured``: every URL kwarg is an argument, and the route
    is the host's. So is a bound value its parameter refuses: the client
    neither sent it nor can see it.

    ``spec`` may be a
    [`SpecSource`][django_service_specs.specs.spec_source.SpecSource]: it is
    compiled once, first, and every step reads the spec it compiles to.

    **CSRF is the host's middleware**, as for any Django view: nothing here
    exempts a view, and ``csrf_exempt`` is one line for a host that wants it.
    """
    spec = kernel_spec(spec)
    try:
        # The principal first, as ``adispatch`` resolves a ``principal_id``
        # and ``dispatch`` refuses a deactivated principal before its shape
        # check: a deactivated account is refused without learning anything
        # about its request.
        principal = request_principal(request)
        arguments = request_arguments(
            request,
            spec.parameters(),
            url_kwargs=url_kwargs,
            route_kwargs=route_kwargs,
            server_arguments=server_arguments,
        )
        if server_arguments is not None:
            arguments = apply_server_arguments(spec, arguments, server_arguments)
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
        check_asked_input(spec, refused, pool_seeds=pool_seeds)
        return error_response(refused)
