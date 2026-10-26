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

import copy
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
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import PermissionDenied
from django.core.handlers.exception import response_for_exception
from django.http import Http404
from django.middleware.csrf import CsrfViewMiddleware
from django.template import TemplateSyntaxError, engines
from django.template.defaultfilters import time
from django.template.loader import get_template
from django.test import RequestFactory
from django.urls import get_resolver
from django.utils import timezone
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
from docs.examples import htmx as htmx_example
from tests.adapter_app.models import Author, Book, Edition, Status

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

    def test_a_page_naming_no_template_renders_the_shipped_one(self, ada: Any) -> None:
        response = serve(FACTORY.get("/books/new/"), ada).render()
        assert response.template_name == ["django_spec_html/spec_form.html"]
        assert response.content.startswith(b"<!DOCTYPE html>")
        assert b'<button type="submit">Save</button>' in response.content

    def test_a_page_naming_its_own_template_renders_that(self, ada: Any) -> None:
        response = serve(FACTORY.get("/books/add/"), ada).render()
        assert response.template_name == ["spec_form.html"]
        assert response.content.startswith(b'<form method="post">')

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

    def test_every_error_shows_in_one_post(self, ada: Any, author: Author) -> None:
        # The blank title is the shape check's, so the form's own rule about
        # a published book's date answers beside it rather than next time.
        data = posted(author, title="", status=Status.PUBLISHED)
        response = serve(FACTORY.post("/books/new/", data=data), ada).render()
        assert response.status_code == 400
        assert response.context_data["form"].errors == {
            "title": ["This field is required."],
            "__all__": [NEEDS_A_DATE],
        }
        assert response.content.count(b"This field is required.") == 1
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


class TestAPartialUpdate:
    """``change_book_spec``: the same form, every field optional to the browser."""

    def test_its_page_renders_no_required_attribute(self, ada: Any, author: Author) -> None:
        book = Book.objects.create(
            title="The Lathe of Heaven", price=Decimal("9.99"), status=Status.DRAFT, author=author
        )
        response = serve(FACTORY.get(f"/books/{book.pk}/change/"), ada).render()
        assert response.status_code == 200
        assert b"required" not in response.content
        whole = serve(FACTORY.get(f"/books/{book.pk}/edit/"), ada).render()
        assert b"required" in whole.content

    def test_a_blank_field_is_left_as_it_is(self, ada: Any, author: Author) -> None:
        book = Book.objects.create(
            title="The Lathe of Heaven", price=Decimal("9.99"), status=Status.DRAFT, author=author
        )
        data = posted(author, title="", price="15.00")
        response = serve(FACTORY.post(f"/books/{book.pk}/change/", data=data), ada)
        assert (response.status_code, response.url) == (302, "/books/")
        book.refresh_from_db()
        assert (book.title, book.price) == ("The Lathe of Heaven", Decimal("15.00"))


class TestAFormThatTakesKeywords:
    """``shelve_book_spec``: the page's form is built with what ``form_kwargs`` answers."""

    def test_the_page_offers_the_authors_the_validator_accepts(
        self, ada: Any, author: Author
    ) -> None:
        Book.objects.create(
            title="The Lathe of Heaven", price=Decimal("9.99"), status=Status.DRAFT, author=author
        )
        unshelved = Author.objects.create(name="Russ")
        page = serve(FACTORY.get("/books/shelve/"), ada).render()
        assert list(page.context_data["form"].fields["author"].queryset) == [author]
        assert f'value="{unshelved.pk}"'.encode() not in page.content
        response = serve(FACTORY.post("/books/shelve/", data=posted(unshelved)), ada)
        assert response.status_code == 400
        assert list(response.context_data["form"].errors) == ["author"]

    def test_staff_are_offered_every_author(self, author: Author) -> None:
        staff = get_user_model().objects.create_user(username="grace", is_staff=True)
        response = serve(FACTORY.post("/books/shelve/", data=posted(author)), staff)
        assert (response.status_code, response.url) == (302, "/books/")
        assert Book.objects.get().author == author


def drafts(author: Author, *titles: str, status: str = Status.DRAFT) -> list[Book]:
    return [
        Book.objects.create(title=title, price=Decimal("9.99"), status=status, author=author)
        for title in titles
    ]


class TestThePageShowsItsTarget:
    """``withdraw_book_spec`` and ``publish_drafts_spec``, rendered through ``spec_target.html``."""

    def test_a_book_is_named_by_its_title_beside_the_form(self, ada: Any, author: Author) -> None:
        (book,) = drafts(author, "The Lathe of Heaven", status=Status.PUBLISHED)
        response = serve(FACTORY.get(f"/books/{book.pk}/withdraw/"), ada).render()
        assert response.context_data["target"] == book
        assert response.context_data["target_display"] == {
            "label": "The Lathe of Heaven",
            "fields": {"title": "The Lathe of Heaven", "price": "9.99", "status": "Published"},
            "pairs": tuple(
                {"title": "The Lathe of Heaven", "price": "9.99", "status": "Published"}.items()
            ),
        }
        content = response.content.decode()
        assert "<h1>The Lathe of Heaven</h1>" in content
        assert "<dt>status</dt><dd>Published</dd>" in content
        # The handle and the hidden field are never shown.
        assert "<dt>pk</dt>" not in content
        assert "published_on" not in content
        assert 'name="reason"' in content

    def test_a_bulk_page_counts_the_set_and_says_when(self, ada: Any, author: Author) -> None:
        drafts(author, "Earthsea", "Always Coming Home", "Lavinia", "Searoad", "Changing Planes")
        drafts(author, "The Dispossessed", status=Status.PUBLISHED)
        response = serve(FACTORY.get("/books/publish-drafts/"), ada).render()
        context = response.context_data
        assert context["target_count"] == 5
        content = response.content.decode()
        # In the current time zone, as the template's ``time`` filter reads it.
        at = time(timezone.localtime(context["target_counted_at"]), "H:i")
        assert f"5 books selected, counted at {at}." in content
        # The first three, by title, which is the set's own order.
        assert (
            "<ul><li>Always Coming Home</li><li>Changing Planes</li><li>Earthsea</li></ul>"
            in content
        )
        assert "The first 3 are shown." in content

    def test_an_empty_set_is_0_selected_not_a_404(self, ada: Any) -> None:
        response = serve(FACTORY.get("/books/publish-drafts/"), ada).render()
        assert response.status_code == 200
        assert "0 books selected, counted at" in response.content.decode()
        assert response.context_data["target_display"] == []

    def test_the_confirming_post_acts_on_the_set_as_it_is_then(
        self, ada: Any, author: Author
    ) -> None:
        # The page counted two; a third draft arrives before the post, and the
        # post re-runs the service on the set it finds, never the one counted.
        drafts(author, "Earthsea", "Lavinia")
        assert serve(FACTORY.get("/books/publish-drafts/"), ada).context_data["target_count"] == 2
        drafts(author, "Searoad")
        data = {"csrfmiddlewaretoken": "token", "save": "Save", "published_on": "2026-10-26"}
        response = serve(FACTORY.post("/books/publish-drafts/", data=data), ada)
        assert (response.status_code, response.url) == (302, "/books/")
        assert not Book.objects.filter(status=Status.DRAFT).exists()
        assert Book.objects.filter(status=Status.PUBLISHED).count() == 3

    def test_a_refused_post_counts_again(self, ada: Any, author: Author) -> None:
        drafts(author, "Earthsea")
        data = {"csrfmiddlewaretoken": "token", "save": "Save", "published_on": ""}
        response = serve(FACTORY.post("/books/publish-drafts/", data=data), ada).render()
        assert response.status_code == 400
        assert "1 book selected, counted at" in response.content.decode()


class Anything(forms.Form):
    """A form every post is valid for, so each refusal comes from the service that raises it."""

    note = forms.CharField(required=False)


class TestAnUnavailableBook:
    """``StockBook``: the page asks whether the operation is available before anyone posts."""

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_a_branch_the_seeded_check_refuses_is_never_offered_the_page(
        self, ada: Any, author: Author, method: str
    ) -> None:
        # The class-level check names the seed, and is asked with it.
        request = getattr(FACTORY, method)("/branches/nowhere/books/new/", data=posted(author))
        with pytest.raises(PermissionDenied, match=r"^There is no such branch\.$"):
            serve(request, ada)
        assert not Book.objects.exists()

    def test_an_open_branch_is_offered_the_form(self, ada: Any) -> None:
        response = serve(FACTORY.get("/branches/main/books/new/"), ada).render()
        assert response.status_code == 200
        assert "unavailable" not in response.context_data
        assert b"<fieldset>" in response.content
        assert b'class="unavailable"' not in response.content

    def test_a_closed_branch_s_page_says_why_and_disables_the_form(self, ada: Any) -> None:
        response = serve(FACTORY.get("/branches/harbour/books/new/"), ada).render()
        assert response.status_code == 200
        unavailable = response.context_data["unavailable"]
        assert (unavailable.code, unavailable.reason) == (
            "branch_closed",
            "This branch is closed for stocktaking.",
        )
        assert (
            b'<p class="unavailable" data-code="branch_closed">'
            b"This branch is closed for stocktaking.</p>"
        ) in response.content
        assert b"<fieldset disabled>" in response.content

    def test_a_post_to_a_closed_branch_is_refused_with_its_code(
        self, ada: Any, author: Author
    ) -> None:
        # The page's answer is advice; the post is refused as dispatch refuses it.
        response = serve(FACTORY.post("/branches/harbour/books/new/", data=posted(author)), ada)
        response.render()
        assert (response.status_code, response.context_data["code"]) == (409, "branch_closed")
        assert b"This branch is closed for stocktaking." in response.content
        assert b"Choose another branch." in response.content
        assert not Book.objects.exists()

    def test_a_post_to_an_open_branch_adds_the_book_as_a_draft(
        self, ada: Any, author: Author
    ) -> None:
        # The posted status is dropped, and the server's is the book's.
        data = posted(author, status=Status.PUBLISHED, published_on="1974-10-25")
        response = serve(FACTORY.post("/branches/main/books/new/", data=data), ada)
        assert (response.status_code, response.url) == (302, "/books/")
        book = Book.objects.get()
        assert (book.title, book.status) == ("The Dispossessed", Status.DRAFT)

    def test_the_status_the_server_answers_for_renders_disabled(self, ada: Any) -> None:
        response = serve(FACTORY.get("/branches/main/books/new/"), ada).render()
        select = re.search(rb'<select name="status"[^>]*>', response.content)
        assert select is not None
        assert b"disabled" in select.group(0)
        assert b'<option value="draft" selected>' in response.content


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
        AdditionalInputRequired("x", schema={"note": {"type": "string"}}),
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
    def view(service: Any = None, permissions: Any = None) -> Any:
        return SpecFormView.as_view(
            spec=ServiceSpec(
                service=service or (lambda **pool: None),
                permissions=permissions or [NeverPermitted()],
                validator=FormValidator(Anything),
            ),
            template_name="spec_form.html",
            success_url="/",
        )

    @classmethod
    def refused(cls, request: Any, user: Any, view: Any = None) -> Any:
        # A spec refusing the principal, so the view raises the PermissionDenied
        # the handler then answers, as Django's handler would hand it over.
        request.user = AnonymousUser() if user is None else user
        try:
            (view or cls.view())(request)
        except PermissionDenied as refused:
            return response_for_exception(request, refused)
        raise AssertionError("the spec admitted the request")

    def test_a_browser_gets_the_hosts_403_page(self, ada: Any) -> None:
        response = self.refused(FACTORY.post("/books/new/"), ada)
        assert response.status_code == 403
        assert not [name for name in response.headers if name.lower().startswith("hx-")]
        assert response["Vary"] == "HX-Request"

    def test_anonymous_htmx_is_sent_to_sign_in_and_back(self) -> None:
        here = "http://testserver/books/new/"
        request = FACTORY.post("/books/new/", HTTP_HX_CURRENT_URL=here, **HTMX)
        response = self.refused(request, None)
        assert response.status_code == 403
        assert response["HX-Redirect"] == "/accounts/login/?next=http%3A//testserver/books/new/"
        assert response["Vary"] == "HX-Request"

    def test_a_signed_in_htmx_request_reloads_the_page(self, ada: Any) -> None:
        response = self.refused(FACTORY.post("/books/new/", **HTMX), ada)
        assert response.status_code == 403
        assert response["HX-Refresh"] == "true"
        assert "HX-Redirect" not in response
        assert response["Vary"] == "HX-Request"

    def test_a_page_refusing_on_get_answers_the_reload_with_its_403(self, ada: Any) -> None:
        # The class-level check refuses the page itself, so the reload the
        # handler asked for is refused again, and the browser gets the 403 page.
        reload = self.refused(FACTORY.get("/books/new/"), ada)
        assert reload.status_code == 403
        assert "HX-Refresh" not in reload

    def test_a_denial_only_the_operation_raises_reloads_an_empty_form(self, ada: Any) -> None:
        # Raised by the service, so it does not recur on GET: the reload is
        # the form again, and what was typed is gone.
        def refuses(**pool: Any) -> None:
            raise NotPermitted("Not this one.")

        view = self.view(service=refuses, permissions=[Unrestricted()])
        post = FACTORY.post("/books/new/", data={"note": "typed"}, **HTMX)
        assert self.refused(post, ada, view)["HX-Refresh"] == "true"
        reload = FACTORY.get("/books/new/")
        reload.user = ada
        response = view(reload)
        assert response.status_code == 200
        assert not response.context_data["form"].is_bound


class NeverPermitted(PermissionCheck):
    message = "Not for you."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


class TestTurningHtmxOn:
    """The page's own template with its form as a ``spec-form`` fragment, and a ``spec-result``.

    From Django 6.0 under the suite's own settings, which name nothing of
    django-template-partials; below it, under the ``TEMPLATES`` the page tells
    a project on an older Django to add, and nothing else.
    """

    @pytest.fixture(autouse=True)
    def urlconf(self, settings: Any) -> None:
        settings.ROOT_URLCONF = HTMX_URLCONF
        if django.VERSION < (6, 0):
            settings.TEMPLATES = htmx_example.TEMPLATES

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

    @pytest.mark.skipif(django.VERSION >= (6, 0), reason="the tag is Django's own from 6.0")
    def test_below_6_the_builtin_is_what_parses_the_tag(self, settings: Any) -> None:
        # The loader alone, as installing the app sets it up: the address
        # resolves, and the page's template fails at its first partialdef.
        engine = copy.deepcopy(htmx_example.TEMPLATES)
        del engine[0]["OPTIONS"]["builtins"]
        settings.TEMPLATES = engine
        with pytest.raises(TemplateSyntaxError, match="'partialdef'"):
            get_template("spec_form_htmx.html#spec-form")

    @pytest.mark.skipif(django.VERSION < (6, 0), reason="partialdef is a library below 6.0")
    def test_from_6_a_load_of_the_library_breaks_the_template(self) -> None:
        # Why the page names the tag as a builtin below 6.0 rather than loading it.
        with pytest.raises(TemplateSyntaxError, match="partials"):
            engines["django"].from_string("{% load partials %}")

    def test_a_browser_s_success_is_still_the_redirect(self, ada: Any, author: Author) -> None:
        response = self.serve(FACTORY.post("/books/new/", data=posted(author)), ada)
        assert (response.status_code, response.url) == (302, "/books/")


DISCARD = "/books/discard-drafts/"
IN_A_DIALOG = "/books/discard-drafts/in-a-dialog/"
ASKED = "{count} drafts will be deleted. Confirm to proceed."


def discarding(**fields: Any) -> dict[str, Any]:
    """What the discard page sends, the token and the button included."""
    return {"csrfmiddlewaretoken": "token", "save": "Save", "reason": "Tidy", **fields}


class TestTheConfirmationPage:
    """``discard_drafts_spec``: asked until confirmed, through ``spec_confirm.html``."""

    @pytest.fixture(autouse=True)
    def shelf(self, author: Author) -> None:
        drafts(author, "Earthsea", "Lavinia")
        drafts(author, "The Dispossessed", status=Status.PUBLISHED)

    def test_the_page_counts_the_set_and_holds_the_box_back(self, ada: Any) -> None:
        response = serve(FACTORY.get(DISCARD), ada).render()
        content = response.content.decode()
        assert response.status_code == 200
        assert "2 drafts selected, counted at" in content
        assert "<ul><li>Earthsea</li><li>Lavinia</li></ul>" in content
        assert 'name="reason"' in content
        assert 'name="confirmed"' not in content

    def test_a_post_is_asked_and_nothing_is_deleted(self, ada: Any) -> None:
        response = serve(FACTORY.post(DISCARD, data=discarding()), ada).render()
        content = response.content.decode()
        assert response.status_code == 422
        assert content.count(ASKED.format(count=2)) == 1
        assert 'name="confirmed"' in content
        # The template's own reading of the question, from ``requested_input``.
        assert ">Discard them</button>" in content
        assert "Yes, discard them" in content
        assert 'name="reason" value="Tidy"' in content
        assert Book.objects.filter(status=Status.DRAFT).count() == 2

    def test_the_question_counts_the_set_as_it_is_then(self, ada: Any, author: Author) -> None:
        assert serve(FACTORY.get(DISCARD), ada).context_data["target_count"] == 2
        drafts(author, "Searoad")
        response = serve(FACTORY.post(DISCARD, data=discarding()), ada).render()
        assert response.context_data["target_count"] == 3
        content = response.content.decode()
        assert "3 drafts selected, counted at" in content
        assert ASKED.format(count=3) in content
        assert response.context_data["requested_input"]["message"] == ASKED.format(count=3)

    def test_the_confirming_post_deletes_them(self, ada: Any) -> None:
        data = discarding(confirmed="on")
        response = serve(FACTORY.post(DISCARD, data=data), ada)
        assert (response.status_code, response.url) == (302, "/books/")
        assert list(Book.objects.values_list("title", flat=True)) == ["The Dispossessed"]

    def test_htmx_gets_the_form_with_the_box_held_back(self, ada: Any) -> None:
        response = serve(FACTORY.get(DISCARD, **HTMX), ada).render()
        assert response.content.lstrip().startswith(b"<form")
        assert b'name="confirmed"' not in response.content

    def test_htmx_is_asked_in_place_at_200(self, ada: Any) -> None:
        response = serve(FACTORY.post(DISCARD, data=discarding(), **HTMX), ada).render()
        assert response.status_code == 200
        assert response.content.lstrip().startswith(b"<form")
        assert b"<h1>" not in response.content
        assert b'name="confirmed"' in response.content
        assert b">Discard them</button>" in response.content
        # In place: no header sends the answer anywhere but the form's target.
        assert not [name for name in response.headers if name.lower().startswith("hx-")]
        assert Book.objects.filter(status=Status.DRAFT).count() == 2

    def test_htmx_s_confirming_post_deletes_them(self, ada: Any) -> None:
        data = discarding(confirmed="on")
        response = serve(FACTORY.post(DISCARD, data=data, **HTMX), ada)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/books/")
        assert not Book.objects.filter(status=Status.DRAFT).exists()


class TestAConfirmationInADialog:
    """``spec_confirm_dialog.html``: the question swapped out of band, into a dialog outside the form."""

    @pytest.fixture(autouse=True)
    def shelf(self, author: Author, settings: Any) -> None:
        drafts(author, "Earthsea", "Lavinia")
        if django.VERSION < (6, 0):
            settings.TEMPLATES = htmx_example.TEMPLATES

    @staticmethod
    def form_and_rest(content: bytes) -> tuple[bytes, bytes]:
        form, _, rest = content.partition(b"</form>")
        return form, rest

    def test_the_fragment_carries_the_dialog_out_of_band(self, ada: Any) -> None:
        response = serve(FACTORY.post(IN_A_DIALOG, data=discarding(), **HTMX), ada).render()
        assert response.status_code == 200
        assert not [name for name in response.headers if name.lower().startswith("hx-")]
        form, rest = self.form_and_rest(response.content)
        assert form.lstrip().startswith(b'<form id="discard-drafts"')
        # The question is the dialog's, never the form's.
        assert ASKED.format(count=2).encode() not in form
        assert b'name="confirmed"' not in form
        assert b'<dialog id="confirm-discard" hx-swap-oob="true" open>' in rest
        assert ASKED.format(count=2).encode() in rest
        # Outside the form, so each control names the form it posts with.
        assert b'name="confirmed" form="discard-drafts"' in rest
        assert b'name="save" form="discard-drafts"' in rest
        assert b'name="reason" value="Tidy"' in form

    def test_no_dialog_before_a_question(self, ada: Any) -> None:
        response = serve(FACTORY.get(IN_A_DIALOG, **HTMX), ada).render()
        assert b"<dialog" not in response.content
        assert b'name="confirmed"' not in response.content
        assert b'name="reason"' in response.content

    def test_the_page_has_one_dialog_to_swap_into(self, ada: Any) -> None:
        page = serve(FACTORY.get(IN_A_DIALOG), ada).render().content
        assert page.count(b'<dialog id="confirm-discard">') == 1
        asked = serve(FACTORY.post(IN_A_DIALOG, data=discarding()), ada).render()
        assert asked.status_code == 422
        assert asked.content.count(b'id="confirm-discard"') == 1
        assert b'<dialog id="confirm-discard" hx-swap-oob="true" open>' in asked.content

    def test_what_the_dialog_posts_deletes_them(self, ada: Any) -> None:
        # The ticked box and the button, the form's own fields beside them.
        data = discarding(confirmed="on")
        response = serve(FACTORY.post(IN_A_DIALOG, data=data, **HTMX), ada)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/books/")
        assert not Book.objects.filter(status=Status.DRAFT).exists()


def editions(**fields: Any) -> dict[str, Any]:
    """What the edition page sends: the token, and the fields and button a test names."""
    return {"csrfmiddlewaretoken": "token", **fields}


class TestAPostThatDoesNotCommit:
    """``editions/new/``: the author picker and "Show their books" re-render, "Add" commits."""

    @pytest.fixture
    def shelved(self, author: Author) -> tuple[Book, Book]:
        theirs = Book.objects.create(title="Earthsea", price=Decimal("9.99"), author=author)
        other = Author.objects.create(name="Butler")
        return theirs, Book.objects.create(title="Kindred", price=Decimal("8.00"), author=other)

    def test_the_page_offers_no_book_until_an_author_is_chosen(self, ada: Any) -> None:
        response = serve(FACTORY.get("/editions/new/"), ada).render()
        assert list(response.context_data["form"].fields["book"].queryset) == []
        # The picker posts the form itself, and the button Enter presses,
        # the first, is the one that commits.
        assert b'hx-trigger="change"' in response.content
        assert response.content.index(b'name="add"') < response.content.index(b'name="refresh"')

    def test_show_their_books_re_renders_the_form_with_theirs(
        self, ada: Any, author: Author, shelved: tuple[Book, Book]
    ) -> None:
        data = editions(author=str(author.pk), refresh="Show their books")
        response = serve(FACTORY.post("/editions/new/", data=data), ada).render()
        assert response.status_code == 200
        assert list(response.context_data["form"].fields["book"].queryset) == [shelved[0]]
        assert response.context_data["refreshed"] is True
        # Nothing validated: the book and number left blank are not called required.
        assert b"This field is required." not in response.content
        assert not Edition.objects.exists()

    def test_the_picker_under_htmx_is_the_form_alone(
        self, ada: Any, author: Author, shelved: tuple[Book, Book]
    ) -> None:
        # What htmx sends for the select's own request: the form's values,
        # no button, and the select's name as ``HX-Trigger-Name``.
        headers = {**HTMX, "HTTP_HX_TRIGGER_NAME": "author"}
        request = FACTORY.post("/editions/new/", data=editions(author=str(author.pk)), **headers)
        response = serve(request, ada).render()
        assert response.status_code == 200
        assert response.content.startswith(b'<form method="post" hx-post=""')
        assert b"<h1>" not in response.content
        assert response.context_data["refreshed"] is True
        assert b"errorlist" not in response.content
        assert list(response.context_data["form"].fields["book"].queryset) == [shelved[0]]
        assert not Edition.objects.exists()

    def test_add_commits_and_says_so(
        self, ada: Any, author: Author, shelved: tuple[Book, Book]
    ) -> None:
        data = editions(author=str(author.pk), book=str(shelved[0].pk), number="2", add="Add")
        request = FACTORY.post("/editions/new/", data=data)
        # The store the messages middleware puts on a request.
        request._messages = CookieStorage(request)
        response = serve(request, ada)
        assert (response.status_code, response.url) == (302, "/books/")
        assert Edition.objects.get().number == 2
        assert [each.message for each in request._messages] == ["Edition 2 added."]

    def test_a_book_by_another_author_is_refused_on_add(
        self, ada: Any, author: Author, shelved: tuple[Book, Book]
    ) -> None:
        # The Validator's form is narrowed by the same post, so the commit is too.
        data = editions(author=str(author.pk), book=str(shelved[1].pk), number="2", add="Add")
        response = serve(FACTORY.post("/editions/new/", data=data), ada)
        assert response.status_code == 400
        assert list(response.context_data["form"].errors) == ["book"]
        assert not Edition.objects.exists()
