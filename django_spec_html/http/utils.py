"""What the HTTP entry points share: the answer, and the view's half; and the status map the page shares.

``dispatch_request`` and ``adispatch_request`` differ only in how they reach
the kernel, so everything either does to a result after dispatch is written
once here. What either reads off a request before dispatch - the principal,
the route, the arguments - is public, each in its own module, since a host's
own view reads them by the same rules. The async one reads the request in an
executor hop and builds its responses on the loop, which is why the read and
the answer are separate functions: the read may query, and the answer never
does.

``SpecView`` and ``AsyncSpecView`` likewise differ only in their handlers, so
everything else about them is ``SpecViewBase``. Its checks of the request's
hooks are functions here rather than its methods - ``check_request_hooks``,
``checked_binding``, ``checked_seeds``, ``refuse_server_owned`` - because
``SpecFormView``, which is no ``SpecViewBase``, reads the request by the same
rules and calls the same code.

``REFUSAL_STATUSES`` is the one map from a refusal to its status and its
outcome, read by ``error_response`` and by ``SpecFormView``, so a refused post
answers at the status a JSON client reads for the same refusal.
"""

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
    check_requested_input,
    check_server_arguments,
    kernel_spec,
)

from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
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
"""Every refusal this package answers, its status and its outcome: read top to bottom, first match wins.

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


def refusal_status(refused: DispatchError | ServiceError) -> int:
    """The status ``REFUSAL_STATUSES`` answers ``refused`` with."""
    return next(status for kind, status, _ in REFUSAL_STATUSES if isinstance(refused, kind))


def refusal_outcome(refused: DispatchError | ServiceError) -> Outcome:
    """The outcome ``REFUSAL_STATUSES`` reads ``refused`` as, from the row its status is read from."""
    return next(outcome for kind, _, outcome in REFUSAL_STATUSES if isinstance(refused, kind))


def field_map(detail: Any) -> Mapping[Any, Any]:
    """A ``ServiceValidationError``'s detail as a field map, the shape every 400 body is."""
    if isinstance(detail, Mapping):
        return detail
    if isinstance(detail, list):
        return {NON_FIELD_ERRORS: list(detail)}
    # Anything else is one message: a string, or a lazy one. A lazy string is
    # not a ``str``, so a ladder asking "is it a string?" and reading the rest
    # as a sequence would take it apart into characters.
    return {NON_FIELD_ERRORS: [detail]}


def check_asked_input(
    spec: ServiceSpec | SelectorSpec | SpecSource, refused: Exception, *, pool_seeds: PoolSeeds
) -> None:
    """Refuse, as the service's own error, a request for input no route could hear answered.

    A 422 body carries the schema a service asked under, and a client builds
    its next call from it. A schema naming a key the closed argument set would
    refuse, a key the server fills, or a shape a transport cannot render, is
    not the caller's mistake, so it surfaces as the ``ImproperlyConfigured`` it
    is rather than as a well-formed answer that cannot be answered. Anything
    else passes.

    ``pool_seeds`` are the ones the call was dispatched with, declared and
    per-request alike: a seed ``get_pool_seeds(request)`` supplies fills its
    key as surely as one the view declares. It is required, because the
    kernel's default is no seeds at all, so a caller leaving it out would check
    against none. The tests holding it are
    ``test_a_schema_naming_a_key_the_requests_seeds_fill_is_the_services_error``,
    in ``test_dispatch_request`` and ``test_adispatch_request``.
    """
    if isinstance(refused, AdditionalInputRequired):
        check_requested_input(spec, refused.schema, pool_seeds=pool_seeds)


def not_found_response() -> JsonResponse:
    """A not-found result, as djangorestframework-services answers one: 404 and a ``detail``.

    The message is translated where it is answered. It is DRF's wording, which
    Django's own catalog does not carry, so a project translating it adds it
    to its own catalog - or has it already, where DRF is installed.
    """
    return JsonResponse({"detail": gettext("Not found.")}, status=404)


def success_response(
    spec: ServiceSpec | SelectorSpec, body: Any, success_status: int | None
) -> HttpResponse:
    """The presented body, bare, at the caller's status or at the default one.

    The default is djangorestframework-services': 204 for a service with
    nothing to present, 200 otherwise. A read whose value is ``None`` - an
    ``allow_none`` retrieve that found nothing - answers ``null``, at 200 or
    at the caller's status, because ``None`` is its value rather than the
    absence of one. Each condition of what counts as nothing to present is
    held by its own test:
    ``test_a_selector_that_allows_none_answers_null_at_200`` (the spec's kind)
    and ``test_a_service_with_a_body_is_200`` (the body).

    A 204 carries no body, whichever way it was chosen: it is the status that
    says there is none, and a client may not read one. Like an empty body at
    any other status, it carries no ``Content-Type``, which would describe
    nothing, as DRF drops it.

    **A service with nothing to present, at a caller's status other than
    204, answers an empty body with no** ``Content-Type``, as
    djangorestframework-services answers it: DRF renders ``None`` as no bytes
    and then drops the header, which would describe nothing. The status stays
    the caller's, even where an output selector's re-read found nothing,
    which djangorestframework-services answers 204 whatever the caller named.
    That is a departure on purpose: the caller named the status, and the
    kernel already says what an empty re-read is, the value ``None``. Held by
    ``test_an_empty_re_read_keeps_the_callers_status``.
    """
    nothing = isinstance(spec, ServiceSpec) and body is None
    if success_status is None:
        success_status = 204 if nothing else 200
    if success_status == 204 or nothing:
        # No body either way: a 204 says there is none, whatever was presented.
        # ``HttpResponse`` stamps ``text/html`` on whatever it is given, and an
        # empty body is no HTML document. Each side is held by its own test:
        # test_a_204_carries_no_body_and_no_content_type (a read's body at 204)
        # and test_a_service_presenting_nothing_answers_an_empty_body_at_the_callers_status.
        response = HttpResponse(status=success_status)
        del response["Content-Type"]
        return response
    # ``safe=False`` because a list is as much a body as an object is; the
    # default encoder renders lazy translations, decimals, dates and UUIDs.
    return JsonResponse(body, status=success_status, safe=False)


def answered_methods(spec: Any, methods: Sequence[str] | None, *, label: str) -> tuple[str, ...]:
    """The lowercase methods a spec view dispatches on: the ones named, or its spec's default.

    A read answers GET and HEAD and a write POST, which is what each means to
    an HTTP client. A view that names ``methods`` answers exactly those,
    spelled in either case and returned in ``ANSWERABLE``'s order.

    ``spec`` may be a
    [`SpecSource`][django_service_specs.specs.spec_source.SpecSource], and its
    default is that of the spec it compiles to. Checked when the view is
    built, so a view with no spec, one whose declaration does not compile, or
    one naming a method it could never answer, fails when the URLconf is imported rather
    than on the first request to reach it.

    Raises:
        ImproperlyConfigured: ``spec`` is not a spec and does not compile to
            one, or ``methods`` names one
            outside ``ANSWERABLE``.
    """
    try:
        # Compiled here, when the URLconf is imported, so a declaration that
        # does not compile fails there too, and the cache answers every request.
        compiled = kernel_spec(spec)
    except TypeError as error:
        raise ImproperlyConfigured(f"{label}.as_view() needs a spec: {error}") from error
    # ``kernel_spec`` tells a spec by its compiling to itself, which an object
    # that is no spec can also do; it would otherwise fail on the first request.
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
    """Everything ``SpecView`` and ``AsyncSpecView`` share: the attributes, the hooks and the methods served.

    Each subclass adds one handler, sync or async, bound to every method name.
    Neither subclasses the other, because an async handler cannot override a
    sync one: Django serves a class async only when all of its handlers are.

    **Two hooks read the request** before dispatch, each defaulting to the
    attribute of its name: ``get_pool_seeds(request)`` and
    ``get_server_arguments(request)``. Django builds one view instance per
    request, so either may read ``self.request`` as well.
    """

    spec: ServiceSpec | SelectorSpec | SpecSource | None = None
    success_status: int | None = None
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    methods: Sequence[str] | None = None
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None
    server_arguments: ServerArguments | None = None

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the configuration checked, once, for every request.

        What is checked here fails when the URLconf is imported, in this order:

        1. the spec and the methods;
        2. both hooks are plain ``def``: neither is awaited, so an
           ``async def`` one would answer a coroutine;
        3. ``route_kwargs``: not a bare string, and every target a mapping
           names and every name a tuple allows is a parameter the spec
           declares, read from ``spec.parameters()``;
        4. ``server_arguments``, by the kernel's ``check_server_arguments``
           against the view's declared ``pool_seeds``. What
           ``get_pool_seeds`` answers per request is not what it sees;
        5. no target or allowed name of ``route_kwargs`` is a name
           ``server_arguments`` answers for, since the route may not
           contradict the binding.

        What only a request can show is checked on each request: a captured
        kwarg the spec does not declare, two kwargs filling one parameter (a
        view mounted on two routes may capture one on each), a capture of a
        name the binding answers for, and the hooks' answers, in
        ``served_hooks``.
        """
        # Django's own check of the keywords first, so a misspelt ``sepc=`` is
        # refused as the typo it is rather than read as a view with no spec.
        view = super().as_view(**initkwargs)
        spec = initkwargs.get("spec", cls.spec)
        # The spec before everything that reads it, so a view with no spec is
        # refused as one rather than by a later check failing to read it.
        answered_methods(spec, initkwargs.get("methods", cls.methods), label=cls.__name__)
        check_request_hooks(
            cls,
            initkwargs,
            spec,
            remedy=" AsyncSpecView runs it off the event loop, in an executor hop.",
        )
        return view

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        """Django's ``setup``, then the methods this view serves.

        ``http_method_names`` is what ``View.dispatch`` sends to
        ``http_method_not_allowed`` and what ``Allow`` is built from, so
        narrowing it per instance gives both a 405 and a header that agree with
        the spec, with no handler to remove. OPTIONS stays, as Django answers it.
        """
        super().setup(request, *args, **kwargs)
        methods = answered_methods(self.spec, self.methods, label=type(self).__name__)
        self.http_method_names = [*methods, "options"]

    def served_spec(self) -> ServiceSpec | SelectorSpec | SpecSource:
        """``spec``, which ``as_view`` refused to build a view without."""
        return cast("ServiceSpec | SelectorSpec | SpecSource", self.spec)

    def get_pool_seeds(self, request: HttpRequest) -> PoolSeeds:
        """The seeds this request's call is resolved with: ``pool_seeds`` unless overridden.

        Seed what the request carries rather than the request itself -
        ``tenant=lambda: request.tenant`` - so a spec reading ``tenant`` reads
        it from every transport that can seed one. A spec callable requiring a
        ``request`` raises ``TypeError`` on every reader that has none: MCP,
        the agent tools, the CLI and a task. That is why no ``request`` seed is
        registered by default.
        """
        return self.pool_seeds

    def get_server_arguments(self, request: HttpRequest) -> ServerArguments | None:
        """The values the server answers for on this request's call: ``server_arguments`` unless overridden.

        The names it takes are the declared ``server_arguments``' names, which
        ``as_view`` checked; only the values may vary per request. Each value
        is checked against its parameter on every call, by the kernel's
        ``apply_server_arguments``.
        """
        return self.server_arguments

    def served_server_arguments(self, request: HttpRequest) -> ServerArguments | None:
        """``get_server_arguments(request)``, refused unless it binds and withholds what the declared binding does.

        ``as_view`` checked the declared binding alone, and every check but the
        value's own reads only which names are bound and which withheld. So a
        per-call binding naming another name would reach dispatch unchecked,
        one naming fewer would hand a client a name the server answers for,
        and one withholding a name the declared binding binds could withhold a
        parameter no call can do without. Only the values may vary, and the
        kernel's ``apply_server_arguments`` checks those on every call.
        ``None`` binds and withholds nothing, as an empty binding does, so
        either may stand for the other.

        Raises:
            ImproperlyConfigured: the bound names or the withheld ones differ.
        """
        return checked_binding(
            type(self).__name__, self.get_server_arguments(request), self.server_arguments
        )

    def served_hooks(self, request: HttpRequest) -> tuple[ServerArguments | None, PoolSeeds]:
        """What both hooks answer for ``request``, refused where the seeds meet the binding.

        ``served_server_arguments``, then ``get_pool_seeds(request)``. A seed
        the per-call seeds add beyond the declared ``pool_seeds``, under a
        name the binding binds or withholds, is refused: the as_view check
        read only the declared seeds, and a seed taking a bound name would
        answer every request with the binding doing nothing, or a 400. This
        reads names alone, so it also refuses the one pairing the kernel's
        check would allow, a seed beside a Validator field of its name; a
        host wanting that declares the seed in ``pool_seeds``.

        Raises:
            ImproperlyConfigured: the per-call binding's names differ from the
                declared ones, or a per-call seed occupies one of them.
        """
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
    """Refuse, when the URLconf is imported, a view whose request hooks could not serve a request.

    The checks every view reading the request makes once its spec is known
    to be one, in this order: both hooks are plain ``def``; ``route_kwargs``
    names only parameters ``spec`` declares, and is no bare string;
    ``server_arguments`` passes the kernel's ``check_server_arguments``
    against the view's declared ``pool_seeds``; and ``route_kwargs`` fills
    no name the binding answers for. Each is read from ``initkwargs`` first
    and the class second, as ``as_view`` reads every attribute. ``remedy``
    ends the async hook's refusal, for a view with more to say about where
    a hook runs.

    Raises:
        ImproperlyConfigured: an ``async def`` hook, a ``route_kwargs`` or a
            binding the spec cannot honour, or a route filling a bound name.
        TypeError: ``route_kwargs`` is a bare string.
    """
    label = view_class.__name__
    for hook in ("get_pool_seeds", "get_server_arguments"):
        if inspect.iscoroutinefunction(_configured(view_class, initkwargs, hook)):
            raise ImproperlyConfigured(
                f"{label}.{hook} is async, and the view calls it without awaiting it, so "
                f"it would answer a coroutine: define it with def.{remedy}"
            )
    route_kwargs = _configured(view_class, initkwargs, "route_kwargs")
    if route_kwargs is not None:
        # No URL kwargs: what is left is the setting's own check.
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
    """``served``, a hook's per-call binding, refused unless it binds and withholds what ``declared`` does.

    The check when the view was built read the declared binding alone, and
    every check but the value's own reads only which names are bound and
    which withheld. So a per-call binding naming another name would reach
    dispatch unchecked, one naming fewer would hand a client a name the
    server answers for, and one withholding a name the declared binding
    binds could withhold a parameter no call can do without. ``None`` binds
    and withholds nothing, as an empty binding does, so either may stand for
    the other.

    Raises:
        ImproperlyConfigured: the bound names or the withheld ones differ.
    """
    # Two halves compared at once, each held by its own test:
    # test_a_per_call_binding_dropping_a_declared_name_is_refused fails
    # without the bound names, and
    # test_a_per_call_binding_releasing_a_withheld_name_is_refused without
    # the withheld ones.
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
    """``seeds``, a hook's per-call seeds, refused where one beyond ``declared`` takes a name ``served`` answers for.

    The check when the view was built read only the declared seeds, and a
    seed taking a bound name would answer every request with the binding
    doing nothing, or a 400. This reads names alone, so it also refuses the
    one pairing the kernel's check would allow, a seed beside a Validator
    field of its name; a host wanting that declares the seed in
    ``pool_seeds``.

    Raises:
        ImproperlyConfigured: a per-call seed occupies a bound or withheld name.
    """
    bound, withheld = _split(served)
    # Only the seeds beyond the declared ones: the check when the view was
    # built read those, and lets a Validator field named like one be bound,
    # since the field is the caller's argument and the seed keeps the
    # service's parameter of its name. Each part is held by its own test:
    # test_a_per_call_seed_occupying_a_bound_name_is_refused (the bound
    # names), test_a_per_call_seed_occupying_a_withheld_name_is_refused
    # (the withheld ones), and
    # test_a_declared_seed_a_bound_validator_field_shares_a_name_with_is_served
    # (the declared seeds left out).
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
    """Refuse every captured kwarg that would fill a name the server answers for, by its captured name.

    Named as the route captured it, so the refusal says which kwarg to drop,
    whether it fills the name under its own or through a mapping.
    """
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
    """The parameter the captured kwarg ``name`` fills under ``route_kwargs``, or ``None`` if dropped."""
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
    return f"binds {', '.join(sorted(bound)) or 'nothing'} and withholds {', '.join(sorted(withheld)) or 'nothing'}"
