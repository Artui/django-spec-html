"""Serving operations from Django views: a function view, the class form, and their URLconf.

``tests/docs/test_http_examples.py`` resolves these routes with Django's own
resolver and sends each a request, so the page cannot describe a route, a
method or an answer the package does not have.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from django_service_specs import AsyncSpecView, SpecView, adispatch_request, dispatch_request
from docs.examples.declaring import list_notes_spec
from docs.examples.quickstart import rename_note_spec


# --8<-- [start:function_view]
def rename_note(request: HttpRequest, pk: int) -> HttpResponse:
    # The route's pk is an argument beside the body's title, merged last, so a
    # body naming another pk cannot move this view off note ``pk``.
    return dispatch_request(rename_note_spec, request, url_kwargs={"pk": pk})


async def arename_note(request: HttpRequest, pk: int) -> HttpResponse:
    return await adispatch_request(rename_note_spec, request, url_kwargs={"pk": pk})


# --8<-- [end:function_view]


# --8<-- [start:urls]
urlpatterns = [
    # A read answers GET and HEAD, and its query string is its arguments:
    # /notes/?search=draft&ordering=-title
    path("notes/", SpecView.as_view(spec=list_notes_spec)),
    # A write answers POST: the pk from the route, the title from the body.
    path("notes/<int:pk>/rename/", SpecView.as_view(spec=rename_note_spec)),
    # The same write as a PATCH, served from async code.
    path("notes/<int:pk>/", AsyncSpecView.as_view(spec=rename_note_spec, methods=["patch"])),
    # The function views above.
    path("notes/<int:pk>/rename-by-hand/", rename_note),
    path("notes/<int:pk>/arename-by-hand/", arename_note),
]
# --8<-- [end:urls]


# --8<-- [start:csrf]
# A route a script calls, authenticated by the host's own middleware rather than
# a session cookie, so it has no CSRF token to send. Exempting it is the host's
# decision, and one line.
urlpatterns += [
    path("hooks/notes/<int:pk>/rename/", csrf_exempt(SpecView.as_view(spec=rename_note_spec))),
]
# --8<-- [end:csrf]
