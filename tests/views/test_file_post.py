from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django import forms
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import ImproperlyConfigured
from django.http import FileResponse, HttpResponseRedirect, StreamingHttpResponse
from django_service_specs import FilePresenter, FormValidator, ServiceSpec

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from tests.dispatch.utils import OPEN, Record, make_user
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

urlpatterns: list[Any] = []

HTMX = {"HTTP_HX_REQUEST": "true"}


class TitleForm(forms.Form):
    title = forms.CharField()


def as_text(title: str) -> bytes:
    return title.encode()


def chunked(title: str) -> Iterator[bytes]:
    yield title.encode()
    yield b"!"


@pytest.fixture(autouse=True)
def urls(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


TEXT = FilePresenter("text/plain", filename="minutes.txt", render=as_text)


def file_spec(presenter: Any = TEXT, returns: Any = "Minutes", **fields: Any) -> ServiceSpec:
    fields.setdefault("validator", FormValidator(TitleForm))
    return ServiceSpec(service=Record(returns), permissions=OPEN, presenter=presenter, **fields)


def posted(view: Any, headers: Any = None) -> tuple[Any, Any]:
    request = signed_in(FACTORY.post("/", {"title": "Hi"}, **(headers or {})), make_user("ada"))
    request._messages = CookieStorage(request)
    return view(request), request


def body(response: Any) -> bytes:
    try:
        return b"".join(response.streaming_content)
    finally:
        response.close()


def form_view(spec: ServiceSpec, **initkwargs: Any) -> Any:
    initkwargs = {"success_url": "/done/", "success_message": "Saved.", **initkwargs}
    return SpecFormView.as_view(spec=spec, **initkwargs)


class TestAPostOfAFileSpec:
    def test_answers_the_file_as_an_attachment_with_no_redirect_and_no_message(self) -> None:
        response, request = posted(form_view(file_spec()))
        assert isinstance(response, FileResponse)
        assert response["Content-Disposition"] == 'attachment; filename="minutes.txt"'
        assert response["Content-Type"] == "text/plain"
        assert not response.has_header("Location")
        assert body(response) == b"Minutes"
        assert list(get_messages(request)) == []

    def test_an_htmx_post_answers_the_file_with_no_hx_header(self) -> None:
        response, _ = posted(form_view(file_spec()), HTMX)
        assert isinstance(response, FileResponse)
        assert [name for name in response.headers if name.startswith("HX-")] == []
        assert body(response) == b"Minutes"

    def test_as_attachment_false_answers_it_inline(self) -> None:
        response, _ = posted(form_view(file_spec(), as_attachment=False))
        assert response["Content-Disposition"] == 'inline; filename="minutes.txt"'
        response.close()

    def test_an_iterable_of_bytes_is_streamed(self) -> None:
        presenter = FilePresenter("text/plain", filename="minutes.txt", render=chunked)
        response, _ = posted(form_view(file_spec(presenter)))
        assert type(response) is StreamingHttpResponse
        assert response["Content-Disposition"] == 'attachment; filename="minutes.txt"'
        assert body(response) == b"Minutes!"

    def test_a_none_result_redirects_with_the_message_as_any_success(self) -> None:
        response, request = posted(form_view(file_spec(returns=None, allow_none=True)))
        assert isinstance(response, HttpResponseRedirect)
        assert response.url == "/done/"
        assert [str(each) for each in get_messages(request)] == ["Saved."]

    def test_a_spec_presenting_no_file_still_redirects_with_its_message(self) -> None:
        response, request = posted(form_view(file_spec(presenter=None)))
        assert isinstance(response, HttpResponseRedirect)
        assert [str(each) for each in get_messages(request)] == ["Saved."]

    def test_the_confirmation_page_answers_the_file_too(self) -> None:
        view = SpecConfirmView.as_view(spec=file_spec(validator=None), success_url="/done/")
        response, _ = posted(view)
        assert isinstance(response, FileResponse)
        assert body(response) == b"Minutes"


def test_a_many_file_spec_is_refused_at_as_view() -> None:
    with pytest.raises(ImproperlyConfigured, match="is declared many"):
        SpecFormSetView.as_view(spec=file_spec(many=True), success_url="/done/")
