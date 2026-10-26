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
everything else about them is ``SpecViewBase``.

``REFUSAL_STATUSES`` is the one map from a refusal to its status, read by
``error_response`` and by ``SpecFormView``, so a refused post answers at the
status a JSON client reads for the same refusal.
"""

from __future__ import annotations

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

from django_spec_html.http.unsupported_media_type import UnsupportedMediaType

ANSWERABLE: Final = ("get", "head", "post", "put", "patch", "delete")
"""The methods a spec view may be dispatched on, in the order ``Allow`` lists them.

OPTIONS is not one: Django's ``View`` answers it from the methods served, and a
spec has nothing to add to that answer. TRACE is not one either, since
nothing about an operation is a loop-back of the request."""


REFUSAL_STATUSES: Final[tuple[tuple[type[DispatchError] | type[ServiceError], int], ...]] = (
    (InvalidArguments, 400),
    (NotPermitted, 403),
    (PrincipalUnavailable, 403),
    (UnsupportedMediaType, 415),
    (DispatchError, 400),
    (ServiceValidationError, 400),
    (ServiceNotFound, 404),
    (ActionUnavailable, 409),
    (ServiceConflict, 409),
    (AdditionalInputRequired, 422),
    (ServiceError, 422),
)
"""Every refusal this package answers, and its status: read top to bottom, first match wins.

A subclass is listed above its base, or the base's row would answer for it.
Every refusal has a row naming it, even where its status is its base's, so
the map is the whole truth a check reading it needs: the kernel's
``unanswered_refusals`` reads this table, and a row it reports nothing about
is held by ``test_every_refusal_has_a_row_of_its_own``.

``UnsupportedMediaType`` is this package's refusal, which the kernel's check
does not ask about, so ``test_unsupported_media_type_is_415`` holds its row.

Each row's status is held twice over, by the two tables the docs publish:
``test_the_pages_status_table_is_the_one_shipped`` for the JSON views and
``test_the_pages_outcome_table_is_the_one_shipped`` for the page, beside the
row's own tests in ``test_error_response`` and ``test_spec_form_view``.

The dispatch rows sit above the service rows, so a refusal deriving from one
of each answers at its dispatch row's status, on both halves alike."""


def refusal_status(refused: DispatchError | ServiceError) -> int:
    """The status ``REFUSAL_STATUSES`` answers ``refused`` with."""
    return next(status for kind, status in REFUSAL_STATUSES if isinstance(refused, kind))


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


def check_asked_input(spec: ServiceSpec | SelectorSpec | SpecSource, refused: Exception) -> None:
    """Refuse, as the service's own error, a request for input no route could hear answered.

    A 422 body carries the schema a service asked under, and a client builds
    its next call from it. A schema naming a key the closed argument set would
    refuse, or a shape a transport cannot render, is not the caller's mistake,
    so it surfaces as the ``ImproperlyConfigured`` it is rather than as a
    well-formed answer that cannot be answered. Anything else passes.
    """
    if isinstance(refused, AdditionalInputRequired):
        check_requested_input(spec, refused.schema)


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
        """Django's ``as_view``, then the spec, the methods and the binding checked, once, for every request.

        The binding is checked by the kernel's ``check_server_arguments``
        against the view's declared ``pool_seeds``, so one that cannot mean
        what it says fails when the URLconf is imported. What
        ``get_pool_seeds`` answers per request is not what it sees.
        """
        # Django's own check of the keywords first, so a misspelt ``sepc=`` is
        # refused as the typo it is rather than read as a view with no spec.
        view = super().as_view(**initkwargs)
        spec = initkwargs.get("spec", cls.spec)
        # The spec before the binding, so a view with no spec is refused as
        # one rather than by the kernel's check failing to read it.
        answered_methods(spec, initkwargs.get("methods", cls.methods), label=cls.__name__)
        server = initkwargs.get("server_arguments", cls.server_arguments)
        if server is not None:
            check_server_arguments(
                spec, server, pool_seeds=initkwargs.get("pool_seeds", cls.pool_seeds)
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
        served = self.get_server_arguments(request)
        # Two halves compared at once, each held by its own test:
        # test_a_per_call_binding_dropping_a_declared_name_is_refused fails
        # without the bound names, and
        # test_a_per_call_binding_releasing_a_withheld_name_is_refused without
        # the withheld ones.
        if _split(served) != _split(self.server_arguments):
            raise ImproperlyConfigured(
                f"{type(self).__name__}.get_server_arguments() {_described(served)}, where "
                f"server_arguments {_described(self.server_arguments)}. Only the declared "
                "binding was checked when the view was built: declare every name the hook "
                "binds or withholds, and vary only the values."
            )
        return served


def _split(server: ServerArguments | None) -> tuple[frozenset[str], frozenset[str]]:
    """The names ``server`` binds and the names it withholds; neither for no binding."""
    if server is None:
        return frozenset(), frozenset()
    return frozenset(server.values), server.withheld


def _described(server: ServerArguments | None) -> str:
    """What ``server`` binds and withholds, in order, for a refusal."""
    bound, withheld = _split(server)
    return f"binds {', '.join(sorted(bound)) or 'nothing'} and withholds {', '.join(sorted(withheld)) or 'nothing'}"
