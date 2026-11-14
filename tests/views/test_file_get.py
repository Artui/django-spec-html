from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.http import FileResponse, StreamingHttpResponse
from django_service_specs import (
    FilePresenter,
    FileResult,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
)

from django_spec_html.http import utils
from django_spec_html.http.spec_view import SpecView
from django_spec_html.views.spec_selector_view import SpecSelectorView
from tests.dispatch.utils import OPEN, PK, make_user, note_by_pk, notes_of
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def as_text(note: Note) -> bytes:
    return note.title.encode()


def chunked(note: Note) -> Iterator[bytes]:
    yield note.title.encode()
    yield b"!"


TEXT = FilePresenter("text/plain", filename=lambda note: f"note-{note.pk}.txt", render=as_text)


def one(presenter: Any = TEXT, **fields: Any) -> SelectorSpec:
    fields.setdefault("selector", note_by_pk)
    fields.setdefault("reads", PK)
    return SelectorSpec(kind=SelectorKind.RETRIEVE, presenter=presenter, permissions=OPEN, **fields)


def body(response: Any) -> bytes:
    try:
        return b"".join(response.streaming_content)
    finally:
        response.close()


@pytest.fixture
def note() -> Note:
    return Note.objects.create(owner=make_user("ada"), title="Minutes")


def fetched(spec: Any, note: Note, headers: Any = None, **initkwargs: Any) -> Any:
    view = SpecSelectorView.as_view(spec=spec, route_kwargs=("pk",), **initkwargs)
    request = signed_in(FACTORY.get(f"/notes/{note.pk}/", **(headers or {})))
    return view(request, pk=note.pk)


class TestTheSelectorPage:
    def test_a_file_spec_answers_its_bytes_as_an_attachment(self, note: Note) -> None:
        response = fetched(one(), note)
        assert isinstance(response, FileResponse)
        assert response["Content-Disposition"] == f'attachment; filename="note-{note.pk}.txt"'
        assert response["Content-Type"] == "text/plain"
        assert response["Content-Length"] == "7"
        assert body(response) == b"Minutes"

    def test_a_non_ascii_name_is_quoted_as_rfc_5987(self, note: Note) -> None:
        presenter = FilePresenter("text/plain", filename="résumé.txt", render=as_text)
        response = fetched(one(presenter), note)
        response.close()
        quoted = "attachment; filename*=utf-8''r%C3%A9sum%C3%A9.txt"
        assert response["Content-Disposition"] == quoted

    def test_as_attachment_false_answers_inline(self, note: Note) -> None:
        response = fetched(one(), note, as_attachment=False)
        response.close()
        assert response["Content-Disposition"] == f'inline; filename="note-{note.pk}.txt"'

    def test_an_htmx_request_is_answered_the_file_too(self, note: Note) -> None:
        assert body(fetched(one(), note, headers=HTMX)) == b"Minutes"

    def test_an_iterable_streams_with_no_length(self, note: Note) -> None:
        presenter = FilePresenter("text/plain", filename="n.txt", render=chunked)
        response = fetched(one(presenter), note)
        assert type(response) is StreamingHttpResponse
        assert response["Content-Disposition"] == 'attachment; filename="n.txt"'
        assert response["Content-Type"] == "text/plain"
        assert not response.has_header("Content-Length")
        assert body(response) == b"Minutes!"

    def test_an_iterable_of_known_size_says_its_length(self, note: Note) -> None:
        def sized(row: Note) -> FileResult:
            return FileResult(chunked(row), filename="n.txt", size=8)

        response = fetched(one(FilePresenter("text/plain", render=sized)), note)
        assert response["Content-Length"] == "8"
        assert body(response) == b"Minutes!"

    @pytest.mark.parametrize("wrap", [io.BytesIO, ContentFile], ids=["io", "django-file"])
    def test_a_file_object_is_answered(self, note: Note, wrap: Any) -> None:
        presenter = FilePresenter("text/plain", filename="n.txt", render=lambda r: wrap(b"abc"))
        response = fetched(one(presenter), note)
        assert response["Content-Length"] == "3"
        assert body(response) == b"abc"

    def test_a_retrieve_that_found_nothing_is_the_page(self, note: Note) -> None:
        spec = one(allow_none=True, selector=lambda *, pk: None)
        response = fetched(spec, note)
        response.render()
        assert response.status_code == 200
        assert response.context_data["rows"] == []
        assert "row_displays" not in response.context_data

    def test_a_list_file_spec_is_refused_at_as_view(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=notes_of, presenter=TEXT)
        with pytest.raises(ImproperlyConfigured, match="presents a list of rows"):
            SpecSelectorView.as_view(spec=spec)


class TestTheJsonRoute:
    def test_specview_answers_the_file(self, note: Note) -> None:
        view = SpecView.as_view(spec=one(), route_kwargs=("pk",))
        response = view(signed_in(FACTORY.get("/")), pk=note.pk)
        assert response["Content-Disposition"] == f'attachment; filename="note-{note.pk}.txt"'
        assert body(response) == b"Minutes"

    def test_a_list_file_spec_is_refused_at_as_view(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=notes_of,
            presenter=TEXT,
            reads=Parameters.of(Parameter("q", "string")),
        )
        with pytest.raises(ImproperlyConfigured, match="presents a list of rows"):
            SpecView.as_view(spec=spec)


def test_a_success_status_is_kept() -> None:
    response = utils.file_response(FileResult(b"x", filename="x.bin"), status=201)
    response.close()
    assert (response.status_code, response["Content-Type"]) == (201, "application/octet-stream")
