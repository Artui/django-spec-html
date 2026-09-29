"""Serving a form: a spec validated by a Django form, as the page a person fills in.

``tests/docs/test_http_forms_examples.py`` resolves these routes with Django's
own resolver and posts to each as a browser would - the CSRF token and the
submit button included - so the page cannot describe a reading, a redirect or
a refusal the view does not make.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import path, reverse, reverse_lazy

from django_service_specs import DispatchResult, PermissionCheck, ServiceSpec, SpecFormView
from docs.examples.forms_adapter import book_validator
from tests.adapter_app.models import Book


# --8<-- [start:spec]
class IsSignedIn(PermissionCheck):
    message = "Sign in to add a book."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return principal.is_authenticated


def add_book(*, data: dict[str, Any]) -> Book:
    # ``data`` is the form's cleaned_data: a Decimal price, a date, the Author
    # row. ``shelves`` is the form's own, and no column of the book's.
    columns = ("title", "price", "status", "published_on", "author")
    return Book.objects.create(**{name: data[name] for name in columns})


# BookForm, from the forms adapter's page, is the Validator and the page's form.
add_book_spec = ServiceSpec(service=add_book, permissions=[IsSignedIn()], validator=book_validator)
# --8<-- [end:spec]


def books(request: HttpRequest) -> HttpResponse:
    return HttpResponse("The books.")


def book(request: HttpRequest, pk: int) -> HttpResponse:
    return HttpResponse(f"Book {pk}.")


# --8<-- [start:urls]
class AddBookThenShowIt(SpecFormView):
    spec = add_book_spec
    template_name = "spec_form.html"

    def get_success_url(self, result: DispatchResult) -> str:
        # The row the service created, which no static success_url can name.
        return reverse("book", kwargs={"pk": result.value.pk})


urlpatterns = [
    path(
        "books/new/",
        SpecFormView.as_view(
            spec=add_book_spec,
            template_name="spec_form.html",
            success_url=reverse_lazy("books"),
        ),
    ),
    path("books/add/", AddBookThenShowIt.as_view()),
    path("books/", books, name="books"),
    path("books/<int:pk>/", book, name="book"),
]
# --8<-- [end:urls]
