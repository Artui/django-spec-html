"""What the HTTP entry points share: the principal, the route, the answer, the view's half.

``dispatch_request`` and ``adispatch_request`` differ only in how they reach
the kernel, so everything either does to a request before dispatch and to a
result after it is written once here. The async one reads the request in an
executor hop and builds its responses on the loop, which is why the two
halves are separate functions: the read may query, and the answer never does.

Nothing here imports ``request_arguments``, which reads the route through
``route_arguments``: a shared helper that did would make the two import each
other.

``SpecView`` and ``AsyncSpecView`` likewise differ only in their handlers, so
everything else about them is ``SpecViewBase``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, cast

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, HttpResponseBase, JsonResponse
from django.utils.decorators import classonlymethod
from django.utils.translation import gettext
from django.views import View

from django_service_specs.authorization.principal_unavailable import PrincipalUnavailable
from django_service_specs.authorization.utils import is_deactivated
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from django_service_specs.specs.selector_spec import SelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.validation.unknown_arguments import UnknownArguments

ANSWERABLE: Final = ("get", "head", "post", "put", "patch", "delete")
"""The methods a spec view may be dispatched on, in the order ``Allow`` lists them.

OPTIONS is not one: Django's ``View`` answers it from the methods served, and a
spec has nothing to add to that answer. TRACE is not one either, since
nothing about an operation is a loop-back of the request."""


def request_principal(request: HttpRequest) -> Any:
    """``request.user``, anonymous included, unless it is a deactivated account.

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
    leaves there, which is a session query, so the async entry point calls this
    off the event loop.

    Raises:
        PrincipalUnavailable: an authenticated user whose ``is_active`` is false.
    """
    user = request.user
    if is_deactivated(user):
        raise PrincipalUnavailable()
    return user


def route_arguments(parameters: Parameters, url_kwargs: Mapping[str, Any] | None) -> dict[str, Any]:
    """The kwargs a route captured, as arguments, each one a parameter the spec declares.

    Every URL kwarg is an argument, so a route capturing one its spec does not
    declare is the host's configuration, wrong for every request the route
    serves, rather than anything a client sent. It is refused as one - Django
    answers it 500 and shows it under ``DEBUG`` - where a client's own
    undeclared argument is a 400. The ``unknown_arguments`` policy does not
    reach it: that policy is about what a client sends, and no client can send
    a route kwarg or stop sending one.

    Returned as a plain copy and uncoerced, so each caller merges it last,
    where the route wins a clash, and coerces it beside what the client sent.

    Raises:
        ImproperlyConfigured: naming every undeclared kwarg.
    """
    route = dict(url_kwargs or {})
    undeclared = sorted(set(route) - parameters.names())
    if undeclared:
        raise ImproperlyConfigured(
            f"The route captures {', '.join(undeclared)}, which the spec does not declare. "
            "Every URL kwarg is an argument: declare each one as a parameter, or leave it "
            "out of url_kwargs."
        )
    return route


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

    Checked when the view is built, so a view with no spec, or one naming a
    method it could never answer, fails when the URLconf is imported rather
    than on the first request to reach it.

    Raises:
        ImproperlyConfigured: ``spec`` is not a spec, or ``methods`` names one
            outside ``ANSWERABLE``.
    """
    if not isinstance(spec, ServiceSpec | SelectorSpec):
        raise ImproperlyConfigured(
            f"{label}.as_view() needs a spec: a ServiceSpec or a SelectorSpec, got "
            f"{type(spec).__name__}."
        )
    if methods is None:
        return ("get", "head") if isinstance(spec, SelectorSpec) else ("post",)
    named = {method.lower() for method in methods}
    unknown = sorted(named - set(ANSWERABLE))
    if unknown:
        raise ImproperlyConfigured(
            f"{label}.methods names {unknown}, which no spec is dispatched on; "
            f"name any of {', '.join(ANSWERABLE)}."
        )
    return tuple(method for method in ANSWERABLE if method in named)


class SpecViewBase(View):
    """Everything ``SpecView`` and ``AsyncSpecView`` share: the attributes and the methods served.

    Each subclass adds one handler, sync or async, bound to every method name.
    Neither subclasses the other, because an async handler cannot override a
    sync one: Django serves a class async only when all of its handlers are.
    """

    spec: ServiceSpec | SelectorSpec | None = None
    success_status: int | None = None
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    methods: Sequence[str] | None = None

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the spec and methods checked, once, for every request."""
        # Django's own check of the keywords first, so a misspelt ``sepc=`` is
        # refused as the typo it is rather than read as a view with no spec.
        view = super().as_view(**initkwargs)
        answered_methods(
            initkwargs.get("spec", cls.spec),
            initkwargs.get("methods", cls.methods),
            label=cls.__name__,
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

    def served_spec(self) -> ServiceSpec | SelectorSpec:
        """``spec``, which ``as_view`` refused to build a view without."""
        return cast("ServiceSpec | SelectorSpec", self.spec)
