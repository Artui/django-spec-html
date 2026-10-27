"""The admin face: two specs served as a ``ModelAdmin``'s actions, through their own pages.

``tests/docs/test_admin_examples.py`` serves this URLconf through Django's
handler as a signed-in staff member, and follows each action from the admin's
own page - the change form's object tools, the changelist's action dropdown -
so the page cannot describe a link, a redirect or a refusal the admin does
not make.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Annotated, Any

from django import forms
from django.contrib import admin
from django.db.models import QuerySet
from django.urls import path
from django_service_specs import (
    DataclassPresenter,
    FieldMarking,
    FormValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_html import SpecAction, SpecActionsMixin
from tests.adapter_app.models import Book, Status


# --8<-- [start:specs]
class IsEditor(PermissionCheck):
    message = "Only an editor may change the catalogue."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return principal.has_perm("adapter_app.change_book")


@dataclasses.dataclass
class BookOut:
    pk: Annotated[int, FieldMarking.handle()]
    title: Annotated[str, FieldMarking.label()]
    status: Status


def book_by_pk(*, pk: int) -> QuerySet[Book]:
    return Book.objects.filter(pk=pk)


def withdraw(*, instance: Book) -> Book:
    instance.status = Status.DRAFT
    instance.save(update_fields=["status"])
    return instance


# One row: an object action, linked from the book's change page.
withdraw_book_spec = ServiceSpec(
    service=withdraw,
    permissions=[IsEditor()],
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=book_by_pk,
        reads=Parameters.of(Parameter("pk", "integer", required=True)),
    ),
    presenter=DataclassPresenter(BookOut),
)


class PriceForm(forms.Form):
    price = forms.DecimalField(max_digits=6, decimal_places=2, min_value=Decimal("0"))


def published(*, pks: list[int]) -> QuerySet[Book]:
    # The selected rows, and of those only the published ones: the selector
    # decides the set, whatever was ticked.
    return Book.objects.filter(pk__in=pks, status=Status.PUBLISHED)


def reprice(*, collection: QuerySet[Book], data: dict[str, Any]) -> int:
    return collection.update(price=data["price"])


# A set: a changelist action, offered in the action dropdown.
reprice_books_spec = ServiceSpec(
    service=reprice,
    permissions=[IsEditor()],
    validator=FormValidator(PriceForm),
    collection_selector_spec=SelectorSpec(
        kind=SelectorKind.LIST,
        selector=published,
        reads=Parameters.of(Parameter("pks", "array", items="integer", required=True)),
    ),
)
# --8<-- [end:specs]


# --8<-- [start:admin]
class BookAdmin(SpecActionsMixin, admin.ModelAdmin):
    spec_actions = (
        SpecAction(
            "withdraw",
            withdraw_book_spec,
            "Withdraw",
            # The change page's object id fills the selector's pk.
            parameter="pk",
            page_kwargs={"display_target": True, "success_message": "Book withdrawn."},
        ),
        SpecAction(
            "reprice",
            reprice_books_spec,
            "Reprice selected %(verbose_name_plural)s",
            # The selected rows' keys fill the selector's pks.
            parameter="pks",
            page_kwargs={"success_message": "Repriced at %(price)s."},
        ),
    )


# --8<-- [end:admin]


# A site of its own, so the suite's other tests see the default one as they
# left it; a project registers the admin on ``admin.site`` as any other.
site = admin.AdminSite(name="admin")
site.register(Book, BookAdmin)

urlpatterns = [path("admin/", site.urls)]
