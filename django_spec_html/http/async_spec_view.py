"""``AsyncSpecView`` - ``SpecView``, served from async code."""

from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse

from django_spec_html.http.adispatch_request import adispatch_request
from django_spec_html.http.utils import SpecViewBase


class AsyncSpecView(SpecViewBase):
    """[`SpecView`][django_spec_html.http.spec_view.SpecView], with every handler async.

    The same attributes, methods, checks and answers, through
    [`adispatch_request`][django_spec_html.http.adispatch_request.adispatch_request].
    Django serves a class-based view async only when every handler it defines
    is ``async def``, and refuses a class that mixes the two, so every method
    a spec may be served on is async here, not only the ones it answers by
    default. Under ASGI this keeps the request off a worker thread until the
    kernel needs one; under WSGI Django runs it in an event loop of its own,
    which costs more than ``SpecView`` and buys nothing.

    ``get_pool_seeds`` and ``get_server_arguments`` stay sync methods, run
    in an executor hop of their own before dispatch, so an override reading
    ``request.user`` or a model is never a query on the event loop.
    ``as_view()`` refuses an ``async def`` one, which would never be awaited.

    Not a subclass of ``SpecView``, for the same reason: an async handler
    cannot stand in for a sync one.
    """

    async def answer(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Dispatch the spec from async code, the route's kwargs as ``url_kwargs``."""
        # The hooks off the loop: a host's override may read ``request.user``,
        # which is a session query the first time it is read.
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
        )

    get = head = post = put = patch = delete = answer
