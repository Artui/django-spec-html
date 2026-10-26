"""The page example, posted to as a browser posts, and as htmx posts.

``docs/pages.md`` includes its spec and URLconf from ``docs/examples/spec_form.py``
and its template from the suite's own, and its htmx template and 403 handler
from ``tests/templates/spec_form_htmx.html`` and ``docs/examples/htmx.py``. Each test resolves a path against that
URLconf, as Django's handler would, and posts what the rendered page sends -
the CSRF token and the named submit button included - so the page cannot
describe a reading, a redirect or a refusal the view does not make. Where the
page states an outcome, the assertion here is that statement.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import django
import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.handlers.exception import response_for_exception
from django.http import Http404
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory
from django.urls import get_resolver
from django_service_specs import (
    ActionUnavailable,
    AdditionalInputRequired,
    DispatchError,
    FormValidator,
    InvalidArguments,
    NotPermitted,
    PermissionCheck,
    PrincipalUnavailable,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    Unrestricted,
)

from django_spec_html import SpecFormView, UnsupportedMediaType
from django_spec_html.http.utils import refusal_outcome
from tests.adapter_app.models import Author, Book, Status

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
URLCONF = "docs.examples.spec_form"
RESOLVER = get_resolver(URLCONF)
FACTORY = RequestFactory()
HTMX_URLCONF = "docs.examples.htmx"
HTMX = {"HTTP_HX_REQUEST": "true"}
NEEDS_A_DATE = "A published book needs its publication date."


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    # ``reverse_lazy`` and ``reverse`` in the example read the project's URLconf.
    settings.ROOT_URLCONF = URLCONF


@pytest.fixture
def ada() -> Any:
    return get_user_model().objects.create_user(username="ada")


@pytest.fixture
def author() -> Author:
    return Author.objects.create(name="Le Guin")


def serve(request: Any, user: Any = None) -> Any:
    """``request`` through the example URLconf, as Django's handler would route it."""
    request.user = AnonymousUser() if user is None else user
    match = RESOLVER.resolve(request.path_info)
    return match.func(request, *match.args, **match.kwargs)


def posted(author: Author, **fields: Any) -> dict[str, Any]:
    """What the rendered page sends for a book, the token and the button included."""
    return {
        "csrfmiddlewaretoken": "token",
        "save": "Save",
        "title": "The Dispossessed",
        "price": "12.50",
        "status": Status.DRAFT,
        "author": str(author.pk),
        **fields,
    }


class TestGet:
    def test_a_signed_in_user_is_offered_the_form(self, ada: Any) -> None:
        response = serve(FACTORY.get("/books/new/"), ada).render()
        assert response.status_code == 200
        assert not response.context_data["form"].is_bound
        for name in ("title", "price", "published_on", "shelves", "csrfmiddlewaretoken"):
            assert f'name="{name}"'.encode() in response.content

    def test_anonymous_is_never_offered_the_page(self) -> None:
        # The class-level check the post would run, run first: Django's own
        # PermissionDenied, which the host's 403 page answers.
        with pytest.raises(PermissionDenied, match=r"^Sign in to add a book\.$"):
            serve(FACTORY.get("/books/new/"))

    def test_anonymous_is_never_shown_the_page_for_a_malformed_post(self, author: Author) -> None:
        # Refused before the post is read, as the page is: re-rendered, a
        # malformed post would list every author the choice field offers.
        data = {**posted(author, title=""), "author": "99999"}
        with pytest.raises(PermissionDenied, match=r"^Sign in to add a book\.$"):
            serve(FACTORY.post("/books/new/", data=data))


class TestAnUpdatePage:
    def test_the_page_shows_the_book_it_edits(self, ada: Any, author: Author) -> None:
        book = Book.objects.create(
            title="The Lathe of Heaven", price=Decimal("9.99"), status=Status.DRAFT, author=author
        )
        response = serve(FACTORY.get(f"/books/{book.pk}/edit/"), ada).render()
        form = response.context_data["form"]
        assert not form.is_bound
        assert (form["title"].value(), form["price"].value(), form["author"].value()) == (
            "The Lathe of Heaven",
            Decimal("9.99"),
            author.pk,
        )
        assert b'value="The Lathe of Heaven"' in response.content

    def test_a_book_that_does_not_exist_has_no_page(self, ada: Any) -> None:
        with pytest.raises(Http404):
            serve(FACTORY.get("/books/404/edit/"), ada)

    def test_its_post_updates_the_book(self, ada: Any, author: Author) -> None:
        book = Book.objects.create(
            title="The Lathe of Heaven", price=Decimal("9.99"), status=Status.DRAFT, author=author
        )
        data = posted(author, title="The Word for World Is Forest")
        response = serve(FACTORY.post(f"/books/{book.pk}/edit/", data=data), ada)
        assert (response.status_code, response.url) == (302, "/books/")
        book.refresh_from_db()
        assert book.title == "The Word for World Is Forest"


class TestPost:
    def test_a_book_is_created_and_the_post_redirected(self, ada: Any, author: Author) -> None:
        # The date is in the form's input format rather than ISO, and the
        # multi-select sends a list, as a browser posts both.
        data = posted(author, published_on="10/25/1974", shelves=["fiction", "poetry"])
        response = serve(FACTORY.post("/books/new/", data=data), ada)
        assert (response.status_code, response.url) == (302, "/books/")
        book = Book.objects.get()
        assert (book.title, book.price, book.published_on, book.author) == (
            "The Dispossessed",
            Decimal("12.50"),
            datetime.date(1974, 10, 25),
            author,
        )

    def test_the_subclass_redirects_to_the_book_it_created(self, ada: Any, author: Author) -> None:
        response = serve(FACTORY.post("/books/add/", data=posted(author)), ada)
        assert response.url == f"/books/{Book.objects.get().pk}/"

    def test_the_forms_own_rule_refuses_once(self, ada: Any, author: Author) -> None:
        data = posted(author, status=Status.PUBLISHED)
        response = serve(FACTORY.post("/books/new/", data=data), ada).render()
        assert response.status_code == 400
        assert response.context_data["form"].non_field_errors() == [NEEDS_A_DATE]
        assert response.content.count(NEEDS_A_DATE.encode()) == 1
        assert not Book.objects.exists()

    def test_a_blank_required_field_is_refused_once(self, ada: Any, author: Author) -> None:
        response = serve(FACTORY.post("/books/new/", data=posted(author, title="")), ada)
        assert response.status_code == 400
        assert response.context_data["form"].errors == {"title": ["This field is required."]}

    def test_the_hosts_middleware_refuses_a_post_without_a_token(
        self, ada: Any, author: Author
    ) -> None:
        request = FACTORY.post("/books/new/", data=posted(author))
        request.user = ada
        match = RESOLVER.resolve(request.path_info)
        refused = CsrfViewMiddleware(match.func).process_view(request, match.func, (), {})
        assert refused is not None
        assert refused.status_code == 403


class Anything(forms.Form):
    """A form every post is valid for, so each refusal comes from the service that raises it."""

    note = forms.CharField(required=False)


def _outcome_table() -> dict[str, tuple[str, str, str]]:
    """The page's outcome table, as ``{class name: (outcome, page, fragment)}``."""
    page = (ROOT / "docs" / "pages.md").read_text()
    table = page[page.index("| Refusal | Outcome | The page answers | The fragment answers |") :]
    answers: dict[str, tuple[str, str, str]] = {}
    for line in table.splitlines()[2:]:
        if not line.startswith("|"):
            break
        refusals, outcome, on_the_page, as_a_fragment = (
            cell.strip() for cell in line.split("|")[1:5]
        )
        for name in re.findall(r"`(\w+)`", refusals):
            answers[name] = (outcome, on_the_page, as_a_fragment)
    return answers


def _answer(exc: Exception, ada: Any, headers: Mapping[str, str]) -> str:
    """What the view does with ``exc`` raised by the service, as the table words it."""

    def service(**pool: Any) -> None:
        raise exc

    spec = ServiceSpec(
        service=service, permissions=[Unrestricted()], validator=FormValidator(Anything)
    )
    # A fragment in a file of its own, so the column is held on every Django.
    view = SpecFormView.as_view(
        spec=spec,
        template_name="spec_form.html",
        fragment_template_name="spec_fragment.html",
        success_url="/",
    )
    request = FACTORY.post("/", data={}, **headers)
    request.user = ada
    try:
        response = view(request)
    except PermissionDenied:
        return "`PermissionDenied`"
    except Http404:
        return "`Http404`"
    shown = "the form fragment" if response.template_name == ["spec_fragment.html"] else "the form"
    return f"{response.status_code}, {shown} re-rendered"


def test_the_pages_outcome_table_is_the_one_shipped(ada: Any) -> None:
    # The service raises each, after a form every post is valid for, so every
    # refusal reaches the view the same way and each row is read against what
    # the view does with it, as a page and as a fragment.
    examples: list[Any] = [
        InvalidArguments({}),
        ServiceValidationError("x"),
        NotPermitted(),
        PrincipalUnavailable(),
        ServiceNotFound(),
        ServiceConflict(),
        ActionUnavailable(code="x"),
        ServiceError(),
        AdditionalInputRequired("x", schema={"type": "object"}),
        UnsupportedMediaType(),
        DispatchError(),
    ]
    assert _outcome_table() == {
        type(exc).__name__: (
            refusal_outcome(exc).value,
            _answer(exc, ada, {}),
            _answer(exc, ada, HTMX),
        )
        for exc in examples
    }


class TestHandler403:
    """The host's 403 handler from the page, reached as Django's handler reaches it."""

    @pytest.fixture(autouse=True)
    def urlconf(self, settings: Any) -> None:
        settings.ROOT_URLCONF = HTMX_URLCONF

    @staticmethod
    def refused(request: Any, user: Any) -> Any:
        # A spec refusing the principal, so the view raises the PermissionDenied
        # the handler then answers, as Django's handler would hand it over.
        request.user = AnonymousUser() if user is None else user
        try:
            SpecFormView.as_view(
                spec=ServiceSpec(
                    service=lambda **pool: None,
                    permissions=[NeverPermitted()],
                    validator=FormValidator(Anything),
                ),
                template_name="spec_form.html",
                success_url="/",
            )(request)
        except PermissionDenied as refused:
            return response_for_exception(request, refused)
        raise AssertionError("the spec admitted the request")

    def test_a_browser_gets_the_hosts_403_page(self, ada: Any) -> None:
        response = self.refused(FACTORY.post("/books/new/"), ada)
        assert response.status_code == 403
        assert not [name for name in response.headers if name.lower().startswith("hx-")]

    def test_anonymous_htmx_is_sent_to_sign_in_and_back(self) -> None:
        here = "http://testserver/books/new/"
        request = FACTORY.post("/books/new/", HTTP_HX_CURRENT_URL=here, **HTMX)
        response = self.refused(request, None)
        assert response.status_code == 403
        assert response["HX-Redirect"] == "/accounts/login/?next=http%3A//testserver/books/new/"

    def test_a_signed_in_htmx_request_reloads_the_page(self, ada: Any) -> None:
        response = self.refused(FACTORY.post("/books/new/", **HTMX), ada)
        assert response.status_code == 403
        assert response["HX-Refresh"] == "true"
        assert "HX-Redirect" not in response


class NeverPermitted(PermissionCheck):
    message = "Not for you."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


@pytest.mark.skipif(django.VERSION < (6, 0), reason="the #name address is Django 6.0's")
class TestTurningHtmxOn:
    """The page's own template with its form as a ``spec-form`` fragment, and a ``spec-result``."""

    @pytest.fixture(autouse=True)
    def urlconf(self, settings: Any) -> None:
        settings.ROOT_URLCONF = HTMX_URLCONF

    @staticmethod
    def serve(request: Any, user: Any) -> Any:
        request.user = user
        match = get_resolver(HTMX_URLCONF).resolve(request.path_info)
        return match.func(request, *match.args, **match.kwargs)

    def test_a_browser_gets_the_whole_page(self, ada: Any) -> None:
        response = self.serve(FACTORY.get("/books/new/"), ada).render()
        assert b"<h1>New book</h1>" in response.content
        assert b'hx-post="/books/new/"' in response.content

    def test_htmx_gets_the_form_alone(self, ada: Any) -> None:
        response = self.serve(FACTORY.get("/books/new/", **HTMX), ada).render()
        assert response.status_code == 200
        assert response.content.lstrip().startswith(b"<form")
        assert b"<h1>" not in response.content

    def test_a_refused_post_is_the_form_alone_at_200(self, ada: Any, author: Author) -> None:
        data = posted(author, status=Status.PUBLISHED)
        response = self.serve(FACTORY.post("/books/new/", data=data, **HTMX), ada).render()
        assert response.status_code == 200
        assert response.content.lstrip().startswith(b"<form")
        assert response.content.count(NEEDS_A_DATE.encode()) == 1

    def test_a_success_is_the_result_in_place(self, ada: Any, author: Author) -> None:
        response = self.serve(FACTORY.post("/books/new/", data=posted(author), **HTMX), ada)
        response.render()
        assert response.status_code == 200
        assert response["HX-Trigger"] == "spec-succeeded"
        assert response.content.strip() == b'<p id="saved">Added The Dispossessed.</p>'
        assert Book.objects.get().title == "The Dispossessed"

    def test_a_browser_s_success_is_still_the_redirect(self, ada: Any, author: Author) -> None:
        response = self.serve(FACTORY.post("/books/new/", data=posted(author)), ada)
        assert (response.status_code, response.url) == (302, "/books/")
