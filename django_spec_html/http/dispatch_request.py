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
    html_call_context,
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
    view: Any = None,
) -> HttpResponse:
    """Dispatch ``spec`` for ``request`` and answer it as JSON: the function a view calls."""
    spec = kernel_spec(spec)
    call_context = html_call_context(request, view)
    try:
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
        result = dispatch(
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
        return success_response(spec, present(spec, result), success_status)
    except (DispatchError, ServiceError) as refused:
        check_asked_input(spec, refused, pool_seeds=pool_seeds, call_context=call_context)
        return error_response(refused)
