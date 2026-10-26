"""``SpecView`` - a spec served as a Django class-based view."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse

from django_spec_html.http.dispatch_request import dispatch_request
from django_spec_html.http.utils import SpecViewBase


class SpecView(SpecViewBase):
    """One spec as a view: each request it answers goes through ``dispatch_request``.

    Every attribute can be set on a subclass or passed to ``as_view()``:

        urlpatterns = [
            path("notes/", SpecView.as_view(spec=list_notes_spec)),
            path("notes/<int:pk>/rename/", SpecView.as_view(spec=rename_note_spec)),
        ]

    **The methods follow the spec.** A ``SelectorSpec`` answers GET and HEAD, a
    ``ServiceSpec`` POST, and ``methods`` replaces that with the ones it names,
    from GET, HEAD, POST, PUT, PATCH and DELETE. Any other method is Django's
    own 405, whose ``Allow`` header lists exactly the methods served, and
    OPTIONS is Django's own answer. **The URL kwargs are arguments**, merged
    last so the route wins a clash, which means the spec declares each one:
    a route capturing a kwarg its spec does not declare raises
    ``ImproperlyConfigured`` on the first request it serves, since that is the
    host's configuration rather than anything a client sent. ``route_kwargs``
    renames or drops the kwargs a spec does not declare, as an ``include()``
    prefix such as ``<org>/`` needs.

    **The request reaches the call through two hooks**, each defaulting to the
    attribute of its name: ``get_pool_seeds(request)`` for the seeds, so a
    tenant or a locale middleware resolved reaches a permission check, a
    selector or a Validator; and ``get_server_arguments(request)`` for the
    values the server answers for, which outrank whatever the client sent and
    whose names are the declared ``server_arguments``' own.

    ``as_view()`` checks the configuration when the view is built, so a view
    with no spec, an ``async def`` hook, ``route_kwargs`` naming a parameter
    the spec does not declare or one the binding answers for, or a binding
    that cannot mean what it says, fails when the URLconf is imported rather
    than on its first request. Each request checks what only it can show: a
    captured kwarg the spec does not declare or the binding answers for, two
    kwargs filling one parameter, and the hooks' answers (see
    ``SpecViewBase.as_view``).

    **CSRF is the host's middleware**, as for any Django view. Nothing here is
    exempted, and ``csrf_exempt(SpecView.as_view(...))`` is one line for a host
    that wants it.

    Attributes:
        spec: The operation served. Required.
        success_status: The status of a success, or ``None`` for the default:
            204 for a service with nothing to present, 200 otherwise.
        unknown_arguments: What becomes of an argument no parameter declares.
        pool_seeds: The seeds the spec's callables are resolved with, unless
            ``get_pool_seeds`` answers others for a request.
        methods: The HTTP methods served, in either case, or ``None`` for the
            spec's default.
        route_kwargs: Which URL kwargs fill which parameters: ``None`` for
            every one under its own name, a mapping from a kwarg to the
            parameter it fills or to ``None`` to drop it, or a tuple of the
            names allowed through. See
            [`route_arguments`][django_spec_html.http.route_arguments.route_arguments].
        server_arguments: The kernel's ``ServerArguments`` the server answers
            for, checked at ``as_view()``; its values may vary per request
            through ``get_server_arguments``, and its names may not.
    """

    def answer(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Dispatch the spec for ``request``, the route's kwargs as ``url_kwargs``."""
        server, pool_seeds = self.served_hooks(request)
        return dispatch_request(
            self.served_spec(),
            request,
            url_kwargs=kwargs,
            route_kwargs=self.route_kwargs,
            server_arguments=server,
            success_status=self.success_status,
            pool_seeds=pool_seeds,
            unknown_arguments=self.unknown_arguments,
        )

    # Every method a spec may be served on is the same call; which of them
    # this view answers is ``http_method_names``' business, set in ``setup``.
    get = head = post = put = patch = delete = answer
