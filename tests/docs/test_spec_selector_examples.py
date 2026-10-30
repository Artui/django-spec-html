"""The selector example, served as its own URLconf and requested as a browser requests it.

``docs/selectors.md`` includes its specs, table and URLconf from
``docs/examples/spec_selector.py``. Each test requests a page through the test
client with that URLconf and the shipped templates, and follows the links the
page renders, so the page cannot describe a page, a link, a refusal or a query
count the view does not make. Where the page states an outcome, the
assertion here is that statement.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from asgiref.sync import iscoroutinefunction, sync_to_async
from django.test import AsyncClient, Client
from django.urls import resolve

from docs.examples import spec_selector
from docs.examples.spec_selector import seed
from tests.adapter_app.models import Book, Status

pytestmark = pytest.mark.django_db

URLCONF = "docs.examples.spec_selector"
# A COUNT, the page's rows with every row's flags annotated on them, and the
# author filter's choices as the form draws them.
PAGE_QUERIES = 3


@pytest.fixture(autouse=True)
def project(settings: Any) -> None:
    settings.ROOT_URLCONF = URLCONF
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "django_tables2"]


def page(path: str) -> str:
    response = Client().get(path)
    assert response.status_code == 200, response.content
    return response.content.decode()


def link(rendered: str, rel: str) -> str:
    (href,) = re.findall(rf'<a href="(\?[^"]*)" rel="{rel}">', rendered)
    return href.replace("&amp;", "&")


def test_the_list_pages_the_books_and_its_links_keep_the_filters() -> None:
    seed()

    first = page("/books/?title=Book")

    assert first.count("<li>Book ") == 10
    assert "Page 1 of 3" in first
    second = page("/books/" + link(first, "next"))
    assert "<li>Book 11</li>" in second
    assert link(second, "next") == "?title=Book&page=3"


def test_a_client_chooses_its_page_size_up_to_the_ceiling() -> None:
    seed()

    assert page("/books/?per_page=5").count("<li>Book ") == 5
    assert page("/books/?per_page=500").count("<li>Book ") == 25


def test_a_filter_narrows_and_a_refused_one_is_said_in_the_results() -> None:
    seed()

    assert page("/books/?title=Book+2").count("<li>Book ") == 6
    refused = Client().get("/books/?author=999")
    assert refused.status_code == 400
    assert "author: Select a valid choice." in refused.content.decode()


def test_the_table_offers_withdraw_on_each_published_row_alone() -> None:
    books = seed(6)

    rendered = page("/books/table/")

    published = [book for book in books if book.status == Status.PUBLISHED]
    for book in published:
        assert f'<a href="/books/{book.pk}/withdraw/">Withdraw</a>' in rendered
    assert rendered.count("<span>Not available</span>") == len(books) - len(published)


def test_pausing_withdrawals_takes_every_row_s_button(monkeypatch: pytest.MonkeyPatch) -> None:
    seed(6)
    monkeypatch.setattr(spec_selector, "WITHDRAWALS_PAUSED", True)

    rendered = page("/books/table/")

    assert "Withdraw</a>" not in rendered
    assert rendered.count("<span>Not available</span>") == 6


def test_every_header_link_is_a_sort_the_spec_reads() -> None:
    seed()

    rendered = page("/books/table/?page=2")
    headers = [
        href.replace("&amp;", "&")
        for href in re.findall(r'<th[^>]*>\s*<a href="([^"]*)"', rendered)
    ]

    assert headers == ["?page=2&o=title", "?page=2&o=price"]
    for header in headers:
        assert Client().get("/books/table/" + header).status_code == 200


def test_a_header_always_links_the_ascending_sort() -> None:
    seed()

    rendered = page("/books/table/?o=title")

    assert '<a href="?o=title">Title</a>' in rendered
    assert "o=-title" not in rendered


def test_the_spec_orders_the_rows_and_the_table_never_reorders_them() -> None:
    seed()

    descending = page("/books/table/?o=-price")

    assert descending.index("<td >Book 25</td>") < descending.index("<td >Book 24</td>")


def test_withdrawing_from_the_table_takes_the_row_s_button() -> None:
    (book,) = [each for each in seed(3) if each.status == Status.PUBLISHED][:1]
    client = Client()

    response = client.post(f"/books/{book.pk}/withdraw/", {"submit": ""})

    assert response.status_code == 302
    assert response["Location"] == "/books/table/"
    book.refresh_from_db()
    assert book.status == Status.DRAFT
    assert f'href="/books/{book.pk}/withdraw/"' not in page("/books/table/")


def test_the_instance_page_shows_its_book_and_a_missing_one_is_404() -> None:
    (book, *_) = seed(1)

    assert f"<li>{book.title}</li>" in page(f"/books/{book.pk}/")
    assert Client().get("/books/999/").status_code == 404


@pytest.mark.parametrize("count", [3, 30])
@pytest.mark.parametrize("path", ["/books/", "/books/table/"])
def test_a_page_queries_the_same_for_any_number_of_books(
    count: int, path: str, django_assert_num_queries: Any
) -> None:
    seed(count)
    client = Client()

    with django_assert_num_queries(PAGE_QUERIES):
        rendered = page_of(client, path)

    assert rendered.count("<li>Book ") + rendered.count("<td >Book ") == min(count, 10)


def page_of(client: Client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    return response.content.decode()


def test_seed_makes_every_third_book_a_draft() -> None:
    books = seed(6)

    assert [book.status for book in books] == [
        Status.PUBLISHED,
        Status.PUBLISHED,
        Status.DRAFT,
    ] * 2
    assert Book.objects.count() == 6


def test_the_page_is_a_sync_view() -> None:
    # ``docs/pages.md`` says the pages are sync views, so Django runs one on a
    # thread under ASGI and a template's lazy read never meets the event loop.
    assert not iscoroutinefunction(resolve("/books/").func)


# A transaction of the test's own: the thread the sync view runs on holds a
# connection of its own, which cannot see rows written inside the ordinary
# test transaction.
@pytest.mark.django_db(transaction=True)
async def test_a_sync_page_answers_200_when_requested_through_the_async_client() -> None:
    # ``AsyncClient`` builds an ASGI request and runs it through Django's async
    # handler, which adapts a sync view to a thread as ``ASGIHandler`` does. The
    # page's template reads the database while it renders, so a read on the
    # event loop would raise ``SynchronousOnlyOperation`` here.
    await sync_to_async(seed)()
    response = await AsyncClient().get("/books/")
    assert response.status_code == 200
    assert "Page 1 of 3" in response.content.decode()
