from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse

from django_spec_html.http.dispatch_request import dispatch_request
from django_spec_html.http.utils import SpecViewBase


class SpecView(SpecViewBase):
    """One spec as a view: each request it answers goes through ``dispatch_request``."""

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
            view=self,
        )

    # Every method a spec may be served on is the same call; which of them
    # this view answers is ``http_method_names``' business, set in ``setup``.
    get = head = post = put = patch = delete = answer
