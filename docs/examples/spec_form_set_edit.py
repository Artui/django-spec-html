from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.urls import path, reverse_lazy
from django_service_specs import (
    FormValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_html import SpecFormSetView
from tests.adapter_app.models import Book


# --8<-- [start:spec]
class OneTitleEach(forms.BaseModelFormSet):
    def clean(self) -> None:
        kept = [
            form.cleaned_data.get("title")
            for form in self.forms
            if not self._should_delete_form(form)
        ]
        if len(set(kept)) != len(kept):
            raise ValidationError("Two books share a title.")


CatalogueFormSet = forms.modelformset_factory(
    Book, formset=OneTitleEach, fields=["title", "price", "author"], can_delete=True, extra=1
)


def every_book() -> QuerySet[Book]:
    return Book.objects.all()


def edit_catalogue(*, data: list[dict[str, Any]]) -> None:
    for row in data:
        book = row.pop("id")
        if row.pop("DELETE"):
            book.delete()
        elif book is None:
            Book.objects.create(**row)
        else:
            Book.objects.filter(pk=book.pk).update(**row)


edit_catalogue_spec = ServiceSpec(
    service=edit_catalogue,
    permissions=[Unrestricted()],
    validator=FormValidator(CatalogueFormSet.form, formset_class=CatalogueFormSet),
    many=True,
    collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=every_book),
)
# --8<-- [end:spec]


def books(request: HttpRequest) -> HttpResponse:
    return HttpResponse("The books.")


# --8<-- [start:urls]
urlpatterns = [
    path(
        "books/edit/",
        SpecFormSetView.as_view(spec=edit_catalogue_spec, success_url=reverse_lazy("books")),
    ),
    path("books/", books, name="books"),
]
# --8<-- [end:urls]
