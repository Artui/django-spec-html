"""``SpecView`` - a spec served as a Django class-based view."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse

from django_service_specs.http.dispatch_request import dispatch_request
from django_service_specs.http.utils import SpecViewBase


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
    host's configuration rather than anything a client sent.

    ``as_view()`` checks the spec and the methods when the view is built, so a
    view with no spec fails when the URLconf is imported rather than on its
    first request.

    **CSRF is the host's middleware**, as for any Django view. Nothing here is
    exempted, and ``csrf_exempt(SpecView.as_view(...))`` is one line for a host
    that wants it.

    Attributes:
        spec: The operation served. Required.
        success_status: The status of a success, or ``None`` for the default:
            204 for a service with nothing to present, 200 otherwise.
        unknown_arguments: What becomes of an argument no parameter declares.
        pool_seeds: The seeds the spec's callables are resolved with.
        methods: The HTTP methods served, in either case, or ``None`` for the
            spec's default.
    """

    def answer(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Dispatch the spec for ``request``, the route's kwargs as ``url_kwargs``."""
        return dispatch_request(
            self.served_spec(),
            request,
            url_kwargs=kwargs,
            success_status=self.success_status,
            pool_seeds=self.pool_seeds,
            unknown_arguments=self.unknown_arguments,
        )

    # Every method a spec may be served on is the same call; which of them
    # this view answers is ``http_method_names``' business, set in ``setup``.
    get = head = post = put = patch = delete = answer
