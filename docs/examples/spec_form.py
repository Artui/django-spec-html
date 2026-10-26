"""Serving a form: a spec validated by a Django form, as the page a person fills in.

``tests/docs/test_spec_form_examples.py`` resolves these routes with Django's
own resolver and posts to each as a browser would - the CSRF token and the
submit button included - so the page cannot describe a reading, a redirect or
a refusal the view does not make.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.urls import path, reverse, reverse_lazy
from django_service_specs import (
    Affordance,
    ChangeResult,
    DispatchResult,
    FormValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    PoolSeeds,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    update_from_input,
)

from django_spec_html import SpecFormView
from tests.adapter_app.models import Author, Book, Status


# --8<-- [start:form]
class BookForm(forms.Form):
    title = forms.CharField(max_length=100, help_text="As printed on the cover.")
    price = forms.DecimalField(max_digits=6, decimal_places=2)
    status = forms.ChoiceField(choices=Status.choices)
    published_on = forms.DateField(required=False)
    author = forms.ModelChoiceField(queryset=Author.objects.all())
    shelves = forms.MultipleChoiceField(
        choices=[("fiction", "Fiction"), ("poetry", "Poetry")], required=False
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("status") == Status.PUBLISHED and not cleaned.get("published_on"):
            raise forms.ValidationError("A published book needs its publication date.")
        return cleaned


# The form is read here, once: a field with no JSON type fails on this line.
book_validator = FormValidator(BookForm)
# --8<-- [end:form]


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


# --8<-- [start:update]
def book_by_pk(*, pk: int) -> QuerySet[Book]:
    return Book.objects.filter(pk=pk)


def edit_book(*, instance: Book, data: dict[str, Any]) -> ChangeResult[Book]:
    return update_from_input(instance, data, exclude_fields=["shelves"])


# The same form behind an update: GET finds the book the route names, and the
# page shows its values for the form's fields.
edit_book_spec = ServiceSpec(
    service=edit_book,
    permissions=[IsSignedIn()],
    validator=book_validator,
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=book_by_pk,
        reads=Parameters.of(Parameter("pk", "integer", required=True)),
    ),
)
# --8<-- [end:update]


# --8<-- [start:partial]
# The same update as a partial one: a field left blank is left as it is.
change_book_spec = ServiceSpec(
    service=edit_book,
    permissions=[IsSignedIn()],
    validator=book_validator,
    instance_selector_spec=edit_book_spec.instance_selector_spec,
    partial=True,
)
# --8<-- [end:partial]


# --8<-- [start:keywords]
class OfferedAuthorsForm(BookForm):
    """``BookForm``, offering the authors its constructor is handed."""

    def __init__(self, *args: Any, authors: QuerySet[Author], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["author"].queryset = authors


def offered_authors(*, user: Any) -> dict[str, Any]:
    # Staff add a book by anyone; everyone else by an author already shelved.
    authors = Author.objects.all()
    if not user.is_staff:
        authors = authors.filter(books__isnull=False).distinct()
    return {"authors": authors}


shelve_book_spec = ServiceSpec(
    service=add_book,
    permissions=[IsSignedIn()],
    validator=FormValidator(OfferedAuthorsForm, form_kwargs=offered_authors),
)
# --8<-- [end:keywords]


# --8<-- [start:request]
BRANCHES = {"main", "harbour"}
CLOSED_BRANCHES = {"harbour"}


class AtABranch(PermissionCheck):
    message = "There is no such branch."

    def has_permission(self, principal: Any, spec: Any, *, branch: str) -> bool:
        # A seed, named by the class-level check: the page asks it with the
        # one get_pool_seeds adds, before it offers the form.
        return branch in BRANCHES


def branch_open(*, branch: str) -> bool:
    # ``branch`` is a seed, so every transport serving this spec seeds its own.
    return branch not in CLOSED_BRANCHES


stock_book_spec = ServiceSpec(
    service=add_book,
    permissions=[IsSignedIn(), AtABranch()],
    validator=book_validator,
    affordances=[
        Affordance(
            code="branch_closed",
            reason="This branch is closed for stocktaking.",
            when=branch_open,
        ),
    ],
)


class StockBook(SpecFormView):
    spec = stock_book_spec
    template_name = "spec_form_available.html"
    success_url = reverse_lazy("books")
    # Every route under ``branches/<branch>/`` captures ``branch``, which this
    # spec reads as a seed rather than an argument: drop it, and hold every
    # other kwarg strict.
    route_kwargs = {"branch": None}
    # A book stocked at a branch starts as a draft, whatever the form posts:
    # the status field renders disabled, and the posted value is dropped.
    server_arguments = ServerArguments(values={"status": Status.DRAFT})

    def get_pool_seeds(self, request: HttpRequest) -> PoolSeeds:
        # What the request carries, never the request itself.
        return self.pool_seeds.extend(branch=lambda: self.kwargs["branch"])


# --8<-- [end:request]


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
    path(
        "books/<int:pk>/edit/",
        SpecFormView.as_view(
            spec=edit_book_spec,
            template_name="spec_form.html",
            success_url=reverse_lazy("books"),
        ),
    ),
    path(
        "books/<int:pk>/change/",
        SpecFormView.as_view(
            spec=change_book_spec,
            template_name="spec_form.html",
            success_url=reverse_lazy("books"),
        ),
    ),
    path(
        "books/shelve/",
        SpecFormView.as_view(
            spec=shelve_book_spec,
            template_name="spec_form.html",
            success_url=reverse_lazy("books"),
        ),
    ),
    path("branches/<branch>/books/new/", StockBook.as_view()),
    path("books/", books, name="books"),
    path("books/<int:pk>/", book, name="book"),
]
# --8<-- [end:urls]
