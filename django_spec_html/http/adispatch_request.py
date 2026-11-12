from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asgiref.sync import sync_to_async
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
    adispatch,
    apply_server_arguments,
    apresent,
    kernel_spec,
)

from django_spec_html.http.error_response import error_response
from django_spec_html.http.request_arguments import request_arguments
from django_spec_html.http.request_principal import request_principal
from django_spec_html.http.utils import (
    check_asked_input,
    html_call_context,
    not_found_response,
    success_response,
)


async def adispatch_request(
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
    view: Any = None,
) -> HttpResponse:
    """[`dispatch_request`][django_spec_html.http.dispatch_request.dispatch_request], async.

    The same principal, arguments, call context, statuses and refusals, and
    the same ``view``, reached through
    [`adispatch`][django_service_specs.dispatch.adispatch.adispatch] and
    [`apresent`][django_service_specs.dispatch.apresent.apresent], so the
    kernel's async rule holds: nothing that may query runs on the event loop.

    That includes **reading the request**, which is one executor hop of its
    own before ``adispatch``'s. ``request.user`` is the lazy object
    AuthenticationMiddleware leaves, and its first read is a session query,
    which Django refuses on the loop; Django 4.2, the floor, has no
    ``request.auser()`` to await instead. ``spec.parameters()`` goes in the
    same hop, because a Validator may read a model to declare what it takes,
    and so does applying ``server_arguments``, which reads it again.

    A refusal asking for input is checked in a hop of its own for the same
    reason: the check holds the schema against ``spec.parameters()``, read
    again for that call. Building the response does not query and stays on
    the loop.
    """
    spec = kernel_spec(spec)
    call_context = html_call_context(request, view)
    try:
        principal, arguments = await sync_to_async(_read_request, thread_sensitive=True)(
            spec, request, url_kwargs, route_kwargs, server_arguments, call_context
        )
        result = await adispatch(
            spec,
            principal=principal,
            arguments=arguments,
            grant=grant,
            pool_seeds=pool_seeds,
            unknown_arguments=unknown_arguments,
            call_context=call_context,
        )
        if result.kind == "not_found":
            return not_found_response()
        return success_response(spec, await apresent(spec, result), success_status)
    except (DispatchError, ServiceError) as refused:
        await sync_to_async(check_asked_input, thread_sensitive=True)(
            spec, refused, pool_seeds=pool_seeds, call_context=call_context
        )
        return error_response(refused)


def _read_request(
    spec: ServiceSpec | SelectorSpec,
    request: HttpRequest,
    url_kwargs: Mapping[str, Any] | None,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None,
    server_arguments: ServerArguments | None,
    call_context: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """The principal, then the arguments, in the one executor hop they share."""
    principal = request_principal(request)
    arguments = request_arguments(
        request,
        spec.parameters(call_context=call_context),
        url_kwargs=url_kwargs,
        route_kwargs=route_kwargs,
        server_arguments=server_arguments,
    )
    if server_arguments is not None:
        arguments = apply_server_arguments(
            spec, arguments, server_arguments, call_context=call_context
        )
    return principal, arguments
