from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import get_resolver

from tests.adapter_app.models import Author, Book, Edition

pytestmark = pytest.mark.django_db

URLCONF = "docs.examples.recipes"
FACTORY = RequestFactory()


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = URLCONF


def serve(request: Any) -> Any:
    request.user = get_user_model().objects.get_or_create(username="ada")[0]
    match = get_resolver(URLCONF).resolve(request.path_info)
    return match.func(request, *match.args, **match.kwargs)


@pytest.fixture
def book() -> Book:
    author = Author.objects.create(name="Le Guin")
    return author.books.create(title="The Dispossessed", price="9.00")


def test_a_form_naming_instance_is_handed_the_row(book: Book) -> None:
    form = serve(FACTORY.get(f"/books/{book.pk}/retitle/")).context_data["form"]
    assert form.fields["title"].help_text == "Was The Dispossessed."


def test_a_form_popping_instance_from_kwargs_raises_key_error_on_the_page(book: Book) -> None:
    with pytest.raises(KeyError):
        serve(FACTORY.get(f"/books/{book.pk}/popping/")).render()


def test_a_new_row_takes_its_tenant_and_an_existing_row_keeps_its_own(book: Book) -> None:
    other = Author.objects.create(name="Other")
    kept = other.books.create(title="Kept", price="1.00")
    formset = serve(FACTORY.get("/books/keep/")).context_data["formset"]
    authors = {form.instance.pk: form.instance.author for form in formset.forms}
    assert authors[None].name == "Le Guin"
    assert authors[kept.pk] == other


def test_a_club_route_is_served_by_the_club_spec(book: Book) -> None:
    response = serve(FACTORY.post(f"/clubs/{book.author_id}/add/", {"title": "New"}))
    assert (response.status_code, response.url) == (302, "/done/")
    assert book.author.books.filter(title="New").exists()


def test_a_section_route_is_served_by_the_section_spec(book: Book) -> None:
    response = serve(FACTORY.post(f"/sections/{book.pk}/add/", {"number": "2"}))
    assert (response.status_code, response.url) == (302, "/done/")
    assert Edition.objects.get().book == book


def test_each_area_page_carries_its_own_form(book: Book) -> None:
    club = serve(FACTORY.get(f"/clubs/{book.author_id}/add/")).context_data["form"]
    section = serve(FACTORY.get(f"/sections/{book.pk}/add/")).context_data["form"]
    assert (list(club.fields), list(section.fields)) == (["title"], ["number"])
