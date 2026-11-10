from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse

from django_spec_html.http.adispatch_request import adispatch_request
from django_spec_html.http.utils import SpecViewBase


class AsyncSpecView(SpecViewBase):
    """[`SpecView`][django_spec_html.http.spec_view.SpecView], with every handler async."""

    async def answer(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Dispatch the spec from async code, the route's kwargs as ``url_kwargs``."""
        server, pool_seeds = await sync_to_async(self.served_hooks, thread_sensitive=True)(request)
        return await adispatch_request(
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

    get = head = post = put = patch = delete = answer
