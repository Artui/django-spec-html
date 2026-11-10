from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, cast

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, HttpResponseBase, JsonResponse
from django.utils.decorators import classonlymethod
from django.utils.translation import gettext
from django.views import View
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    NON_FIELD_ERRORS,
    ActionUnavailable,
    AdditionalInputRequired,
    DispatchError,
    InvalidArguments,
    NotPermitted,
    PermissionCheckDenied,
    PoolSeeds,
    PrincipalUnavailable,
    SelectorSpec,
    ServerArguments,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    SpecSource,
    UnknownArguments,
    build_call_context,
    check_requested_input,
    check_server_arguments,
    kernel_spec,
)

from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
from django_spec_html.types.html_transport import HTML_TRANSPORT
from django_spec_html.types.outcome import Outcome

ANSWERABLE: Final = ("get", "head", "post", "put", "patch", "delete")
"""The methods a spec view may be dispatched on, in the order ``Allow`` lists them.

OPTIONS is not one: Django's ``View`` answers it from the methods served, and a
spec has nothing to add to that answer. TRACE is not one either, since
nothing about an operation is a loop-back of the request."""


REFUSAL_STATUSES: Final[
    tuple[tuple[type[DispatchError] | type[ServiceError], int, Outcome], ...]
] = (
    (InvalidArguments, 400, Outcome.INVALID),
    (PermissionCheckDenied, 403, Outcome.DENIED),
    (NotPermitted, 403, Outcome.DENIED),
    (PrincipalUnavailable, 403, Outcome.DENIED),
    (UnsupportedMediaType, 415, Outcome.INVALID),
    (DispatchError, 400, Outcome.FAILED),
    (ServiceValidationError, 400, Outcome.INVALID),
    (ServiceNotFound, 404, Outcome.DENIED),
    (ActionUnavailable, 409, Outcome.FAILED),
    (ServiceConflict, 409, Outcome.FAILED),
    (AdditionalInputRequired, 422, Outcome.NEEDS_INPUT),
    (ServiceError, 422, Outcome.FAILED),
)
"""Every refusal this package answers, its status and outcome: read top down, first match wins.

A subclass is listed above its base, or the base's row would answer for it.
Every refusal has a row naming it, even where its status is its base's, so
the map is the whole truth a check reading it needs: the kernel's
``unanswered_refusals`` reads each column as a table of its own, and a row it
reports nothing about is held by ``test_every_refusal_has_a_row_of_its_own``.

``UnsupportedMediaType`` is this package's refusal, which the kernel's check
does not ask about, so ``test_unsupported_media_type_is_415`` holds its row.

Each row's status is held twice over, by the two tables the docs publish:
``test_the_pages_status_table_is_the_one_shipped`` for the JSON views and
``test_the_pages_outcome_table_is_the_one_shipped`` for the page, beside the
row's own tests in ``test_error_response`` and ``test_spec_form_view``.

The outcome is the third column rather than a second map, so the one table
stays the whole truth. The page reads it to tell a denial, raised for the
host's handlers, from a refusal it re-renders; every other outcome is
answered alike by both wrappers today, so each row's outcome is held on its
own by ``test_every_refusal_has_its_outcome``.

The dispatch rows sit above the service rows, so a refusal deriving from one
of each answers at its dispatch row's status, on both halves alike."""


def html_call_context(request: HttpRequest, view: Any) -> dict[str, Any]:
    """Return the call context every call this package makes is handed, stamped ``html``."""
    return build_call_context(HTML_TRANSPORT, request=request, view=view)


def refusal_status(refused: DispatchError | ServiceError) -> int:
    """The status ``REFUSAL_STATUSES`` answers ``refused`` with."""
    return next(status for kind, status, _ in REFUSAL_STATUSES if isinstance(refused, kind))


def refusal_outcome(refused: DispatchError | ServiceError) -> Outcome:
    """The outcome ``REFUSAL_STATUSES`` reads ``refused`` as, from the status's row."""
    return next(outcome for kind, _, outcome in REFUSAL_STATUSES if isinstance(refused, kind))


def field_map(detail: Any) -> Mapping[Any, Any]:
    """A ``ServiceValidationError``'s detail as a field map, the shape every 400 body is."""
    if isinstance(detail, Mapping):
        return detail
    if isinstance(detail, list):
        return {NON_FIELD_ERRORS: list(detail)}
    return {NON_FIELD_ERRORS: [detail]}


def check_asked_input(
    spec: ServiceSpec | SelectorSpec | SpecSource,
    refused: Exception,
    *,
    pool_seeds: PoolSeeds,
    call_context: dict[str, Any],
) -> None:
    """Refuse, as the service's own error, a request for input no route could hear answered."""
    if isinstance(refused, AdditionalInputRequired):
        check_requested_input(
            spec, refused.schema, pool_seeds=pool_seeds, call_context=call_context
        )


def not_found_response() -> JsonResponse:
    """A not-found result, as djangorestframework-services answers one: 404 and a ``detail``."""
    return JsonResponse({"detail": gettext("Not found.")}, status=404)


def success_response(
    spec: ServiceSpec | SelectorSpec, body: Any, success_status: int | None
) -> HttpResponse:
    """The presented body, bare, at the caller's status or at the default one."""
    nothing = isinstance(spec, ServiceSpec) and body is None
    if success_status is None:
        success_status = 204 if nothing else 200
    if success_status == 204 or nothing:
        response = HttpResponse(status=success_status)
        del response["Content-Type"]
        return response
    return JsonResponse(body, status=success_status, safe=False)


def answered_methods(spec: Any, methods: Sequence[str] | None, *, label: str) -> tuple[str, ...]:
    """The lowercase methods a spec view dispatches on: the ones named, or its spec's default."""
    try:
        compiled = kernel_spec(spec)
    except TypeError as error:
        raise ImproperlyConfigured(f"{label}.as_view() needs a spec: {error}") from error
    if not isinstance(compiled, ServiceSpec | SelectorSpec):
        raise ImproperlyConfigured(
            f"{label}.as_view() needs a spec: {type(compiled).__name__}.as_kernel_spec() answers "
            "itself but it is neither a ServiceSpec nor a SelectorSpec."
        )
    if methods is None:
        return ("get", "head") if isinstance(compiled, SelectorSpec) else ("post",)
    named = {method.lower() for method in methods}
    unknown = sorted(named - set(ANSWERABLE))
    if unknown:
        raise ImproperlyConfigured(
            f"{label}.methods names {unknown}, which no spec is dispatched on; "
            f"name any of {', '.join(ANSWERABLE)}."
        )
    return tuple(method for method in ANSWERABLE if method in named)


class SpecViewBase(View):
    """What ``SpecView`` and ``AsyncSpecView`` share: attributes, hooks and served methods."""

    spec: ServiceSpec | SelectorSpec | SpecSource | None = None
    success_status: int | None = None
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    methods: Sequence[str] | None = None
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None
    server_arguments: ServerArguments | None = None

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the configuration checked, once, for every request."""
        view = super().as_view(**initkwargs)
        spec = initkwargs.get("spec", cls.spec)
        answered_methods(spec, initkwargs.get("methods", cls.methods), label=cls.__name__)
        check_request_hooks(
            cls,
            initkwargs,
            spec,
            remedy=" AsyncSpecView runs it off the event loop, in an executor hop.",
        )
        return view

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        """Django's ``setup``, then the methods this view serves."""
        super().setup(request, *args, **kwargs)
        methods = answered_methods(self.spec, self.methods, label=type(self).__name__)
        self.http_method_names = [*methods, "options"]

    def served_spec(self) -> ServiceSpec | SelectorSpec | SpecSource:
        """``spec``, which ``as_view`` refused to build a view without."""
        return cast("ServiceSpec | SelectorSpec | SpecSource", self.spec)

    def get_pool_seeds(self, request: HttpRequest) -> PoolSeeds:
        """The seeds this request's call is resolved with: ``pool_seeds`` unless overridden."""
        return self.pool_seeds

    def get_server_arguments(self, request: HttpRequest) -> ServerArguments | None:
        """What the server answers for on this call: ``server_arguments``, unless overridden."""
        return self.server_arguments

    def served_server_arguments(self, request: HttpRequest) -> ServerArguments | None:
        """``get_server_arguments(request)``, refused where it strays from the declared binding."""
        return checked_binding(
            type(self).__name__, self.get_server_arguments(request), self.server_arguments
        )

    def served_hooks(self, request: HttpRequest) -> tuple[ServerArguments | None, PoolSeeds]:
        """What both hooks answer for ``request``, refused where the seeds meet the binding."""
        served = self.served_server_arguments(request)
        seeds = checked_seeds(
            type(self).__name__, self.get_pool_seeds(request), self.pool_seeds, served
        )
        return served, seeds


def check_request_hooks(
    view_class: type[View],
    initkwargs: Mapping[str, Any],
    spec: ServiceSpec | SelectorSpec | SpecSource,
    *,
    remedy: str = "",
) -> None:
    """Refuse, at URLconf import, a view whose request hooks could not serve a request."""
    label = view_class.__name__
    for hook in ("get_pool_seeds", "get_server_arguments"):
        if inspect.iscoroutinefunction(_configured(view_class, initkwargs, hook)):
            raise ImproperlyConfigured(
                f"{label}.{hook} is async, and the view calls it without awaiting it, so "
                f"it would answer a coroutine: define it with def.{remedy}"
            )
    route_kwargs = _configured(view_class, initkwargs, "route_kwargs")
    if route_kwargs is not None:
        route_arguments(kernel_spec(spec).parameters(), None, route_kwargs=route_kwargs)
    server = _configured(view_class, initkwargs, "server_arguments")
    if server is not None:
        check_server_arguments(
            spec, server, pool_seeds=_configured(view_class, initkwargs, "pool_seeds")
        )
        filled = sorted(route_targets(route_kwargs) & server.names())
        if filled:
            raise ImproperlyConfigured(
                f"{label}.route_kwargs fills {', '.join(filled)}, which server_arguments "
                "answers for. The route is the host's, and may not contradict the host's "
                "own binding: map the kwarg to None to drop it, or leave the name out of "
                "server_arguments."
            )


def checked_binding(
    label: str, served: ServerArguments | None, declared: ServerArguments | None
) -> ServerArguments | None:
    """``served``, a hook's per-call binding, refused where it strays from ``declared``."""
    if _split(served) != _split(declared):
        raise ImproperlyConfigured(
            f"{label}.get_server_arguments() {_described(served)}, where "
            f"server_arguments {_described(declared)}. Only the declared "
            "binding was checked when the view was built: declare every name the hook "
            "binds or withholds, and vary only the values."
        )
    return served


def checked_seeds(
    label: str, seeds: PoolSeeds, declared: PoolSeeds, served: ServerArguments | None
) -> PoolSeeds:
    """``seeds``, a hook's per-call seeds, refused where one takes a name ``served`` answers."""
    bound, withheld = _split(served)
    occupied = sorted((bound | withheld) & (seeds.names - declared.names))
    if occupied:
        raise ImproperlyConfigured(
            f"{label}.get_pool_seeds() adds {', '.join(occupied)}, which "
            "server_arguments binds or withholds, and the check when the view was built "
            "never saw a seed added per request: declare it in pool_seeds, where that "
            "check reads it, or leave the name to the binding."
        )
    return seeds


def server_names(server: ServerArguments | None) -> frozenset[str]:
    """Every name ``server`` answers for, bound or withheld; none for no binding."""
    return frozenset() if server is None else server.names()


def refuse_server_owned(
    url_kwargs: Mapping[str, Any] | None,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None,
    owned: frozenset[str],
) -> None:
    """Refuse a captured kwarg that would fill a name the server answers for."""
    filling = sorted(name for name in url_kwargs or {} if _filled_by(name, route_kwargs) in owned)
    if filling:
        raise ImproperlyConfigured(
            f"The route captures {', '.join(filling)}, which server_arguments answers for. "
            f"Drop each with route_kwargs={dict.fromkeys(filling)!r}, or leave it out of "
            "server_arguments: the route is the host's, and may not contradict the host's "
            "own binding."
        )


def without(arguments: Mapping[str, Any], names: frozenset[str]) -> dict[str, Any]:
    """``arguments`` with every name in ``names`` left out: the ones the server answers for."""
    return {key: value for key, value in arguments.items() if key not in names}


def _configured(view_class: type[View], initkwargs: Mapping[str, Any], name: str) -> Any:
    """The attribute ``name`` as ``as_view`` builds the view with: passed to it, or the class's."""
    return initkwargs.get(name, getattr(view_class, name))


def _filled_by(
    name: str, route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None
) -> str | None:
    """The parameter a captured ``name`` fills under ``route_kwargs``, or ``None`` if dropped."""
    if route_kwargs is None:
        return name
    if isinstance(route_kwargs, Mapping):
        return route_kwargs.get(name, name)
    return name if name in route_kwargs else None


def route_targets(
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None,
) -> frozenset[str]:
    """Every parameter ``route_kwargs`` names a kwarg into: a mapping's targets, a tuple's names."""
    if route_kwargs is None:
        return frozenset()
    if isinstance(route_kwargs, Mapping):
        return frozenset(target for target in route_kwargs.values() if target is not None)
    return frozenset(route_kwargs)


def _split(server: ServerArguments | None) -> tuple[frozenset[str], frozenset[str]]:
    """The names ``server`` binds and the names it withholds; neither for no binding."""
    if server is None:
        return frozenset(), frozenset()
    return frozenset(server.values), server.withheld


def _described(server: ServerArguments | None) -> str:
    """What ``server`` binds and withholds, in order, for a refusal."""
    bound, withheld = _split(server)
    return (
        f"binds {', '.join(sorted(bound)) or 'nothing'} "
        f"and withholds {', '.join(sorted(withheld)) or 'nothing'}"
    )
