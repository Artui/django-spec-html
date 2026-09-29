"""``adispatch_request`` - ``dispatch_request``, from an async view."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse

from django_service_specs.authorization.grant import Grant
from django_service_specs.dispatch.adispatch import adispatch
from django_service_specs.dispatch.apresent import apresent
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


async def adispatch_request(
    spec: ServiceSpec | SelectorSpec,
    request: HttpRequest,
    *,
    url_kwargs: Mapping[str, Any] | None = None,
    success_status: int | None = None,
    grant: Grant | None = None,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
) -> HttpResponse:
    """[`dispatch_request`][django_service_specs.http.dispatch_request.dispatch_request], from async code.

    The same principal, arguments, statuses and refusals, reached through
    [`adispatch`][django_service_specs.dispatch.adispatch.adispatch] and
    [`apresent`][django_service_specs.dispatch.apresent.apresent], so the
    kernel's async rule holds: nothing that may query runs on the event loop.

    That includes **reading the request**, which is one executor hop of its
    own before ``adispatch``'s. ``request.user`` is the lazy object
    AuthenticationMiddleware leaves, and its first read is a session query,
    which Django refuses on the loop; Django 4.2, the floor, has no
    ``request.auser()`` to await instead. ``spec.parameters()`` goes in the
    same hop, because a Validator may read a model to declare what it takes.
    Building the response does not query and stays on the loop.
    """
    try:
        principal, arguments = await sync_to_async(_read_request, thread_sensitive=True)(
            spec, request, url_kwargs
        )
        result = await adispatch(
            spec,
            principal=principal,
            arguments=arguments,
            grant=grant,
            pool_seeds=pool_seeds,
            unknown_arguments=unknown_arguments,
        )
        if result.kind == "not_found":
            return not_found_response()
        return success_response(spec, await apresent(spec, result), success_status)
    except (DispatchError, ServiceError) as refused:
        return error_response(refused)


def _read_request(
    spec: ServiceSpec | SelectorSpec,
    request: HttpRequest,
    url_kwargs: Mapping[str, Any] | None,
) -> tuple[Any, dict[str, Any]]:
    """The principal, then the arguments, in the one executor hop they share.

    The principal first, as ``adispatch`` resolves a ``principal_id`` before
    its shape check, so a deactivated account is refused without learning
    anything about its request. Both off the loop: the principal may be a
    session query, and ``spec.parameters()`` may be one too, since a
    Validator may read a model to declare what it takes.
    """
    principal = request_principal(request)
    return principal, request_arguments(request, spec.parameters(), url_kwargs=url_kwargs)
