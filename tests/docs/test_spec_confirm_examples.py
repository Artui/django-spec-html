"""The confirmation example, posted to as a browser posts, and as htmx posts.

``docs/confirm.md`` includes its spec and URLconf from
``docs/examples/spec_confirm.py`` and its templates from the suite's own. Each
test resolves a path against that URLconf, as Django's handler would, and
posts what the rendered page sends - the CSRF token and the named submit
button - so the page cannot describe a reading, a redirect or a refusal the
view does not make. Where the page states an outcome, the assertion here is
that statement.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.test import RequestFactory
from django.urls import get_resolver
from django_service_specs import InvalidArguments, Parameter, Parameters, coerce_flat

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from docs.examples.spec_confirm import withdraw_book_spec
from tests.adapter_app.models import Author, Book, Status

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
URLCONF = "docs.examples.spec_confirm"
RESOLVER = get_resolver(URLCONF)
FACTORY = RequestFactory()
HTMX = {"HTTP_HX_REQUEST": "true"}
# What the rendered page posts: its token, and the button pressed.
PRESSED = {"csrfmiddlewaretoken": "token", "withdraw": ""}


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = URLCONF


@pytest.fixture
def ada() -> Any:
    return get_user_model().objects.create_user(username="ada")


@pytest.fixture
def book() -> Book:
    author = Author.objects.create(name="Le Guin")
    return Book.objects.create(
        author=author, title="The Lathe of Heaven", price=Decimal("9.99"), status=Status.PUBLISHED
    )


def serve(request: Any, user: Any = None) -> Any:
    """``request`` through the example URLconf, as Django's handler would route it."""
    request.user = AnonymousUser() if user is None else user
    match = RESOLVER.resolve(request.path_info)
    return match.func(request, *match.args, **match.kwargs)


def withdrawing(book: Book) -> str:
    return f"/books/{book.pk}/withdraw/"


class TestThePage:
    def test_it_names_the_book_and_offers_the_button(self, ada: Any, book: Book) -> None:
        response = serve(FACTORY.get(withdrawing(book)), ada)
        assert response.status_code == 200
        context = response.context_data
        assert "form" not in context
        assert context["target_display"] == {
            "label": "The Lathe of Heaven",
            "fields": {"title": "The Lathe of Heaven", "status": "Published"},
        }
        body = response.render().content
        assert b"Withdraw <strong>The Lathe of Heaven</strong>?" in body
        assert b'<button name="withdraw">Withdraw it</button>' in body

    def test_a_visitor_signed_out_is_refused_before_anything_is_shown(self, book: Book) -> None:
        with pytest.raises(PermissionDenied, match="Sign in to withdraw a book."):
            serve(FACTORY.get(withdrawing(book)))

    def test_a_draft_says_why_and_disables_the_button(self, ada: Any, book: Book) -> None:
        Book.objects.filter(pk=book.pk).update(status=Status.DRAFT)
        response = serve(FACTORY.get(withdrawing(book)), ada)
        assert response.context_data["unavailable"].code == "not_published"
        body = response.render().content
        assert b"Only a published book can be withdrawn." in body
        assert b'<button name="withdraw" disabled>' in body

    def test_htmx_gets_the_fragment(self, ada: Any, book: Book) -> None:
        response = serve(FACTORY.get(withdrawing(book), **HTMX), ada)
        assert response.render().content.startswith(b'<div id="withdraw">')


class TestThePost:
    def test_the_button_withdraws_the_book_and_says_so(self, ada: Any, book: Book) -> None:
        request = FACTORY.post(withdrawing(book), data=PRESSED)
        # The store the messages middleware puts on a request.
        request._messages = CookieStorage(request)
        response = serve(request, ada)
        assert (response.status_code, response.url) == (302, "/books/")
        book.refresh_from_db()
        assert book.status == Status.DRAFT
        assert [each.message for each in request._messages] == ["Book withdrawn."]

    def test_htmx_is_sent_where_the_page_redirects(self, ada: Any, book: Book) -> None:
        response = serve(FACTORY.post(withdrawing(book), data=PRESSED, **HTMX), ada)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/books/")

    def test_a_post_without_the_button_withdraws_nothing(self, ada: Any, book: Book) -> None:
        response = serve(FACTORY.post(withdrawing(book), data={"csrfmiddlewaretoken": "t"}), ada)
        assert (response.status_code, response.context_data["refreshed"]) == (200, True)
        book.refresh_from_db()
        assert book.status == Status.PUBLISHED

    def test_a_key_posted_names_no_book(self, ada: Any, book: Book) -> None:
        other = Book.objects.create(
            author=book.author, title="The Word for World", price=1, status=Status.PUBLISHED
        )
        serve(FACTORY.post(withdrawing(book), data={**PRESSED, "pk": str(other.pk)}), ada)
        book.refresh_from_db()
        other.refresh_from_db()
        assert (book.status, other.status) == (Status.DRAFT, Status.PUBLISHED)

    def test_a_refusal_is_the_page_again_with_its_reason(self, ada: Any, book: Book) -> None:
        # Withdrawn between the page and its post: dispatch asks the
        # affordance again, and its 409 is the authority.
        Book.objects.filter(pk=book.pk).update(status=Status.DRAFT)
        response = serve(FACTORY.post(withdrawing(book), data=PRESSED), ada)
        assert response.status_code == 409
        context = response.context_data
        assert (context["code"], context["errors"]) == (
            "not_published",
            ["Only a published book can be withdrawn."],
        )
        assert b'<p class="error">Only a published book can be withdrawn.</p>' in (
            response.render().content
        )


def test_a_route_parameter_route_kwargs_does_not_name_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured) as refused:
        SpecConfirmView.as_view(
            spec=withdraw_book_spec, template_name="spec_confirm_view.html", success_url="/"
        )
    quoted = (
        "SpecConfirmView requires pk, which no post to a confirmation carries: fill each from "
        "the route, naming it in route_kwargs, or from a pool seed, a kwargs provider or "
        "server_arguments."
    )
    assert str(refused.value) == quoted
    assert quoted in (ROOT / "docs" / "confirm.md").read_text().replace("\n", " ")


def test_a_box_s_own_value_is_one_a_boolean_parameter_refuses() -> None:
    # Why the page reads no answer off a post: a checkbox with no ``value``
    # posts "on", and the kernel reads a boolean as true, false, 1 or 0.
    confirmed = Parameters.of(Parameter("confirmed", "boolean"))
    with pytest.raises(InvalidArguments):
        coerce_flat(confirmed, {"confirmed": "on"})
