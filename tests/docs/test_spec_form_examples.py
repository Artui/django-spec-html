"""The HTTP page's forms example, posted to as a browser posts.

``docs/http.md`` includes its spec and URLconf from ``docs/examples/http_forms.py``
and its template from the suite's own. Each test resolves a path against that
URLconf, as Django's handler would, and posts what the rendered page sends -
the CSRF token and the named submit button included - so the page cannot
describe a reading, a redirect or a refusal the view does not make. Where the
page states an outcome, the assertion here is that statement.
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory
from django.urls import get_resolver

from django_service_specs import (
    DispatchError,
    FormValidator,
    InvalidArguments,
    NotPermitted,
    PrincipalUnavailable,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    SpecFormView,
    Unrestricted,
)
from tests.adapter_app.models import Author, Book, Status

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
URLCONF = "docs.examples.http_forms"
RESOLVER = get_resolver(URLCONF)
FACTORY = RequestFactory()
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


def _outcome_table() -> dict[str, str]:
    """The Forms section's table, as ``{class name: answer}``."""
    page = (ROOT / "docs" / "http.md").read_text()
    table = page[page.index("| Refusal | The form view answers |") :]
    answers: dict[str, str] = {}
    for line in table.splitlines()[2:]:
        if not line.startswith("|"):
            break
        refusals, answer = line.split("|")[1:3]
        for name in re.findall(r"`(\w+)`", refusals):
            answers[name] = answer.strip()
    return answers


def _answered(exc: Exception, ada: Any) -> str:
    """What the view does with ``exc`` raised by the service, as the table words it."""

    def service(**pool: Any) -> None:
        raise exc

    spec = ServiceSpec(
        service=service, permissions=[Unrestricted()], validator=FormValidator(Anything)
    )
    view = SpecFormView.as_view(spec=spec, template_name="spec_form.html", success_url="/")
    request = FACTORY.post("/", data={})
    request.user = ada
    try:
        response = view(request)
    except PermissionDenied:
        return "`PermissionDenied`"
    except Http404:
        return "`Http404`"
    return f"{response.status_code}, the form re-rendered"


def test_the_pages_outcome_table_is_the_one_shipped(ada: Any) -> None:
    # The service raises each, after a form every post is valid for, so every
    # refusal reaches the view the same way and each row is read against what
    # the view does with it.
    examples: list[Exception] = [
        InvalidArguments({}),
        ServiceValidationError("x"),
        NotPermitted(),
        PrincipalUnavailable(),
        ServiceNotFound(),
        ServiceConflict(),
        ServiceError(),
        DispatchError(),
    ]
    assert _outcome_table() == {type(exc).__name__: _answered(exc, ada) for exc in examples}
