"""The formset example, posted to as a browser posts, and as htmx posts.

``docs/formsets.md`` includes its spec and URLconf from
``docs/examples/spec_form_set.py`` and its templates from the suite's own.
Each test resolves a path against that URLconf, as Django's handler would, and
posts what the rendered page sends - the CSRF token, the management form and
the pressed button - so the page cannot describe a reading, a refusal or a
success the view does not make. Where the page states an outcome, the
assertion here is that statement.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import get_resolver

from tests.adapter_app.models import Author, Book

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
URLCONF = "docs.examples.spec_form_set"
RESOLVER = get_resolver(URLCONF)
FACTORY = RequestFactory()
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
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


def posted(*rows: Mapping[str, str], button: str = "save", total: int = 3) -> dict[str, str]:
    """What the rendered page sends: the token, the management form, each row, the button pressed."""
    data = {
        "csrfmiddlewaretoken": "token",
        "form-TOTAL_FORMS": str(total),
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "5",
        button: "pressed",
    }
    for index, row in enumerate(rows):
        data.update({f"form-{index}-{key}": value for key, value in row.items()})
    return data


def book(author: Author, title: str, price: str = "9.99") -> dict[str, str]:
    return {"title": title, "price": price, "author": str(author.pk)}


class TestThePage:
    def test_a_signed_in_user_is_offered_three_blank_forms(self, ada: Any) -> None:
        response = serve(FACTORY.get("/books/batch/"), ada).render()
        assert response.status_code == 200
        formset = response.context_data["formset"]
        assert (formset.is_bound, len(formset.forms)) == (False, 3)
        for name in ("form-TOTAL_FORMS", "form-0-title", "form-2-author", "csrfmiddlewaretoken"):
            assert f'name="{name}"'.encode() in response.content

    def test_anonymous_is_never_offered_the_page(self) -> None:
        with pytest.raises(PermissionDenied, match=r"^Sign in to add books\.$"):
            serve(FACTORY.get("/books/batch/"))

    def test_the_page_offers_no_target(self, ada: Any) -> None:
        context = serve(FACTORY.get("/books/batch/"), ada).context_data
        assert not {key for key in context if key.startswith("target")}


class TestThePost:
    def test_the_books_are_added_in_order_and_a_blank_form_sends_no_row(
        self, ada: Any, author: Author
    ) -> None:
        data = posted(book(author, "The Dispossessed"), {}, book(author, "Always Coming Home"))
        request = FACTORY.post("/books/batch/", data=data)
        request._messages = CookieStorage(request)
        response = serve(request, ada)
        assert (response.status_code, response.url) == (302, "/books/")
        assert list(Book.objects.values_list("title", flat=True)) == [
            "The Dispossessed",
            "Always Coming Home",
        ]
        assert [each.message for each in request._messages] == ["2 books were added."]

    def test_a_refusal_lands_on_the_form_that_sent_the_row(self, ada: Any, author: Author) -> None:
        # The blank second form is no row, so the third form's price is the
        # list's row 1, and is placed back on the third form.
        data = posted(book(author, "The Dispossessed"), {}, book(author, "Lavinia", "a lot"))
        response = serve(FACTORY.post("/books/batch/", data=data), ada)
        assert response.status_code == 400
        forms = response.context_data["formset"].forms
        assert [dict(form.errors) for form in forms[:2]] == [{}, {}]
        assert list(forms[2].errors) == ["price"]
        assert not Book.objects.exists()

    def test_more_books_than_the_spec_takes_are_refused_as_a_list(
        self, ada: Any, author: Author
    ) -> None:
        data = posted(*(book(author, f"Book {n}") for n in range(6)), total=6)
        response = serve(FACTORY.post("/books/batch/", data=data), ada)
        assert response.status_code == 400
        [message] = response.context_data["formset"].non_form_errors()
        assert "no more than 5" in message
        assert not Book.objects.exists()

    def test_add_a_row_offers_one_more_form_and_adds_no_book(
        self, ada: Any, author: Author
    ) -> None:
        data = posted(book(author, "The Dispossessed"), button="add")
        response = serve(FACTORY.post("/books/batch/", data=data), ada)
        assert response.status_code == 200
        formset = response.context_data["formset"]
        assert len(formset.forms) == 4
        assert formset.forms[0]["title"].value() == "The Dispossessed"
        assert not Book.objects.exists()


class TestHtmx:
    def test_a_refusal_is_the_form_s_fragment_at_200(self, ada: Any, author: Author) -> None:
        data = posted(book(author, "Lavinia", "a lot"))
        response = serve(FACTORY.post("/books/batch/", data=data, **HTMX), ada).render()
        assert response.status_code == 200
        assert b"<h1>" not in response.content
        assert b'name="form-0-price"' in response.content

    def test_a_success_is_hx_redirect(self, ada: Any, author: Author) -> None:
        data = posted(book(author, "The Dispossessed"))
        response = serve(FACTORY.post("/books/batch/", data=data, **HTMX), ada)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/books/")


def test_the_page_includes_the_example_and_its_templates() -> None:
    # The page shows what this module serves, so a section it drops is seen.
    page = (ROOT / "docs" / "formsets.md").read_text()
    for snippet in (
        "docs/examples/spec_form_set.py:spec",
        "docs/examples/spec_form_set.py:urls",
        "tests/templates/spec_formset_form.html",
    ):
        assert snippet in page
