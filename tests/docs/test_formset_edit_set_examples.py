from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.cookie import CookieStorage
from django.test import RequestFactory
from django.urls import get_resolver

from tests.adapter_app.models import Author, Book

pytestmark = pytest.mark.django_db

URLCONF = "docs.examples.spec_form_set_edit"
RESOLVER = get_resolver(URLCONF)
FACTORY = RequestFactory()


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = URLCONF


@pytest.fixture
def shelf() -> list[Book]:
    author = Author.objects.create(name="Le Guin")
    titles = ("The Dispossessed", "The Lathe of Heaven", "Always Coming Home")
    return [author.books.create(title=title, price="9.00") for title in titles]


def serve(request: Any) -> Any:
    request.user = get_user_model().objects.create_user(username="ada")
    request._messages = CookieStorage(request)
    match = RESOLVER.resolve(request.path_info)
    return match.func(request, *match.args, **match.kwargs)


URL = "/books/edit/"


def posted(shelf: list[Book], **changes: str) -> dict[str, str]:
    data = {"form-TOTAL_FORMS": "4", "form-INITIAL_FORMS": "3"}
    for index, book in enumerate(shelf):
        data |= {f"form-{index}-id": str(book.pk), f"form-{index}-title": book.title}
        data[f"form-{index}-price"] = str(book.price)
        data[f"form-{index}-author"] = str(book.author_id)
    return data | changes


def test_the_page_shows_the_author_s_books_and_a_blank_form(shelf: list[Book]) -> None:
    formset = serve(FACTORY.get(URL)).context_data["formset"]
    assert [form.instance.pk for form in formset.forms] == [*(book.pk for book in shelf), None]


def test_a_post_updates_deletes_and_creates(shelf: list[Book]) -> None:
    data = posted(
        shelf,
        **{
            "form-0-price": "12.50",
            "form-1-DELETE": "on",
            "form-3-title": "The Word for World Is Forest",
            "form-3-price": "8.00",
            "form-3-author": str(shelf[0].author_id),
        },
    )
    assert serve(FACTORY.post(URL, data)).status_code == 302
    left = Book.objects.values_list("title", "price")
    assert [(title, str(price)) for title, price in left] == [
        ("The Dispossessed", "12.50"),
        ("Always Coming Home", "9.00"),
        ("The Word for World Is Forest", "8.00"),
    ]


def test_a_title_twice_is_the_formset_s_error(shelf: list[Book]) -> None:
    data = posted(
        shelf,
        **{
            "form-3-title": "The Dispossessed",
            "form-3-price": "8.00",
            "form-3-author": str(shelf[0].author_id),
        },
    )
    response = serve(FACTORY.post(URL, data))
    formset = response.context_data["formset"]
    assert list(formset.non_form_errors()) == ["Two books share a title."]
    assert Book.objects.count() == 3
