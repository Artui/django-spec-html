"""Confirming an operation: a spec with no Validator, as a page showing its target and a button.

``tests/docs/test_spec_confirm_examples.py`` resolves these routes with
Django's own resolver and posts to each as a browser would - the CSRF token and
the submit button included - so the page cannot describe a reading, a redirect
or a refusal the view does not make.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any

from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.urls import path, reverse_lazy
from django_service_specs import (
    Affordance,
    DataclassPresenter,
    FieldMarking,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

# The view's own module until the package root exports it.
from django_spec_html.views.spec_confirm_view import SpecConfirmView
from tests.adapter_app.models import Book, Status


# --8<-- [start:spec]
class IsSignedIn(PermissionCheck):
    message = "Sign in to withdraw a book."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return principal.is_authenticated


@dataclasses.dataclass
class BookOut:
    """A book as the operation returns it, and as its page names it."""

    pk: Annotated[int, FieldMarking.handle()]  # another call's input: never shown
    title: Annotated[str, FieldMarking.label()]  # the name a page calls it by
    status: Status


def book_by_pk(*, pk: int) -> QuerySet[Book]:
    return Book.objects.filter(pk=pk)


def withdraw(*, instance: Book) -> Book:
    instance.status = Status.DRAFT
    instance.save(update_fields=["status"])
    return instance


# No Validator: the route names the book, and there is nothing else to send.
withdraw_book_spec = ServiceSpec(
    service=withdraw,
    permissions=[IsSignedIn()],
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=book_by_pk,
        reads=Parameters.of(Parameter("pk", "integer", required=True)),
    ),
    presenter=DataclassPresenter(BookOut),
    affordances=[
        Affordance(
            code="not_published",
            reason="Only a published book can be withdrawn.",
            when=Q(status=Status.PUBLISHED),
        )
    ],
)
# --8<-- [end:spec]


def books(request: HttpRequest) -> HttpResponse:
    return HttpResponse("The books.")


# --8<-- [start:urls]
urlpatterns = [
    path(
        "books/<int:pk>/withdraw/",
        SpecConfirmView.as_view(
            spec=withdraw_book_spec,
            template_name="spec_confirm_view.html",
            fragment_template_name="spec_confirm_view_form.html",
            success_url=reverse_lazy("books"),
            # The parameter the route fills, which as_view() reads to know
            # that a post to this page needs to carry nothing.
            route_kwargs=("pk",),
            # BookOut describes the book withdrawn, so the page names it.
            display_target=True,
            commit_button="withdraw",
            success_message="Book withdrawn.",
        ),
    ),
    path("books/", books, name="books"),
]
# --8<-- [end:urls]
