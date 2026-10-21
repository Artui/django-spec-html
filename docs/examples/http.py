"""Serving operations from Django views: two specs, a function view, the class form, and their URLconf.

``tests/docs/test_http_examples.py`` resolves these routes with Django's own
resolver and sends each a request, so the page cannot describe a route, a
method or an answer the package does not have.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    coerce_flat,
)

from django_spec_html import (
    AsyncSpecView,
    SpecView,
    adispatch_request,
    dispatch_request,
    route_arguments,
)
from tests.dispatch_app.models import Note  # a title, and an owner who is a user


# --8<-- [start:specs]
@dataclass
class Rename:  # what the write takes
    title: str


@dataclass
class NoteOut:  # what both answer, per note
    id: int
    title: str


class IsSignedIn(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return principal.is_authenticated


class IsOwner(IsSignedIn):
    message = "Only the note's owner may rename it."

    def has_object_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        return target.owner_id == principal.pk


def notes_of(*, user: Any, search: str = "", ordering: str = "title") -> QuerySet[Note]:
    return Note.objects.filter(owner=user, title__icontains=search).order_by(ordering)


def rename(*, instance: Note, title: str) -> Note:
    instance.title = title
    instance.save(update_fields=["title"])
    return instance


list_notes_spec = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=notes_of,
    permissions=[IsSignedIn()],
    reads=Parameters.of(
        Parameter("search", "string", help="Part of the title, in any case."),
        Parameter("ordering", "string", choices=["title", "-title"], default="title"),
    ),
    presenter=DataclassPresenter(NoteOut),
)

rename_note_spec = ServiceSpec(
    service=rename,
    permissions=[IsOwner()],
    validator=DataclassValidator(Rename),
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Note.objects.filter(pk=pk),  # the row, or none
        reads=Parameters.of(Parameter("pk", "integer", required=True)),
    ),
    presenter=DataclassPresenter(NoteOut),
)
# --8<-- [end:specs]


# --8<-- [start:function_view]
def rename_note(request: HttpRequest, pk: int) -> HttpResponse:
    # The route's pk is an argument beside the body's title, merged last, so a
    # body naming another pk cannot move this view off note ``pk``.
    return dispatch_request(rename_note_spec, request, url_kwargs={"pk": pk})


async def arename_note(request: HttpRequest, pk: int) -> HttpResponse:
    return await adispatch_request(rename_note_spec, request, url_kwargs={"pk": pk})


# --8<-- [end:function_view]


# --8<-- [start:route_arguments]
def form_arguments(posted: Mapping[str, Any], url_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    # A view reading its fields some other way merges the route last, so the
    # route wins a clash, and types everything in one call.
    parameters = rename_note_spec.parameters()
    return coerce_flat(parameters, {**posted, **route_arguments(parameters, url_kwargs)})


# --8<-- [end:route_arguments]


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
