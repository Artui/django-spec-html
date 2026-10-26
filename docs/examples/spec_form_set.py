"""Serving a formset: a ``many`` spec validated by a Django form, one form a row.

``tests/docs/test_spec_form_set_examples.py`` resolves these routes with
Django's own resolver and posts to each as a browser would - the CSRF token,
the management form and the submit button included - so the page cannot
describe a reading, a refusal or a success the view does not make.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.http import HttpRequest, HttpResponse
from django.urls import path, reverse_lazy
from django_service_specs import FormValidator, PermissionCheck, ServiceSpec

# The view's own module until the root exports it.
from django_spec_html import SpecFormSetView
from tests.adapter_app.models import Author, Book


# --8<-- [start:spec]
class BookRowForm(forms.Form):
    title = forms.CharField(max_length=100)
    price = forms.DecimalField(max_digits=6, decimal_places=2)
    author = forms.ModelChoiceField(queryset=Author.objects.all())


class IsSignedIn(PermissionCheck):
    message = "Sign in to add books."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return principal.is_authenticated


def add_books(*, data: list[dict[str, Any]]) -> list[Book]:
    # ``data`` is a list: each row's cleaned data, in the order posted.
    return [Book.objects.create(**row) for row in data]


# A many spec: its rows travel as one list under ``items``, each validated by
# the form, and the service runs once for them all. At most five a call.
add_books_spec = ServiceSpec(
    service=add_books,
    permissions=[IsSignedIn()],
    validator=FormValidator(BookRowForm),
    many=True,
    many_max_length=5,
)
# --8<-- [end:spec]


def books(request: HttpRequest) -> HttpResponse:
    return HttpResponse("The books.")


# --8<-- [start:urls]
urlpatterns = [
    path(
        "books/batch/",
        SpecFormSetView.as_view(
            spec=add_books_spec,
            template_name="spec_formset.html",
            fragment_template_name="spec_formset_form.html",
            success_url=reverse_lazy("books"),
            # Three blank forms; a form left blank sends no row.
            extra=3,
            # "Save" runs the operation, and "Add a row" offers one more form.
            commit_button="save",
            add_button="add",
            success_message="%(count)d books were added.",
        ),
    ),
    path("books/", books, name="books"),
]
# --8<-- [end:urls]
