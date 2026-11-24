from __future__ import annotations

from typing import Any

from django import forms
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.urls import path, reverse_lazy
from django_service_specs import (
    FormValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_html import SpecFormSetView, SpecFormView
from tests.adapter_app.models import Author, Book, Edition


# --8<-- [start:instance]
class RetitleForm(forms.Form):
    title = forms.CharField(max_length=100)

    def __init__(self, *args: Any, instance: Book | None = None, **kwargs: Any) -> None:
        # Named, so the page hands it the row; kwargs.pop would raise KeyError.
        super().__init__(*args, **kwargs)
        self.fields["title"].help_text = f"Was {instance.title}." if instance else ""


# --8<-- [end:instance]


class PoppingForm(RetitleForm):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("instance")
        super().__init__(*args, **kwargs)


# --8<-- [start:tenant]
class TenantRowForm(forms.ModelForm):
    class Meta:
        model = Book
        fields = ["title", "price"]

    def __init__(self, *args: Any, author: Author, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk is None:
            self.instance.author = author


TenantFormSet = forms.modelformset_factory(Book, form=TenantRowForm, extra=1)


def authored_by_the_tenant(*, user: Any) -> dict[str, Any]:
    return {"author": Author.objects.get(name="Le Guin")}


# --8<-- [end:tenant]


def every_book() -> QuerySet[Book]:
    return Book.objects.all()


def keep_books(*, data: list[dict[str, Any]]) -> None:
    return None


keep_books_spec = ServiceSpec(
    service=keep_books,
    permissions=[Unrestricted()],
    validator=FormValidator(
        TenantFormSet.form, formset_class=TenantFormSet, form_kwargs=authored_by_the_tenant
    ),
    many=True,
    collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=every_book),
)


# --8<-- [start:areas]
class TitleForm(forms.Form):
    title = forms.CharField(max_length=100)


class NumberForm(forms.Form):
    number = forms.IntegerField(min_value=1)


def author_by_club(*, club: int) -> QuerySet[Author]:
    return Author.objects.filter(pk=club)


def book_by_section(*, section: int) -> QuerySet[Book]:
    return Book.objects.filter(pk=section)


def add_club_book(*, instance: Author, data: dict[str, Any]) -> Book:
    return Book.objects.create(author=instance, title=data["title"], price="1.00")


def add_section_edition(*, instance: Book, data: dict[str, Any]) -> Edition:
    return Edition.objects.create(book=instance, number=data["number"])


club_spec = ServiceSpec(
    service=add_club_book,
    permissions=[Unrestricted()],
    validator=FormValidator(TitleForm),
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=author_by_club,
        reads=Parameters.of(Parameter("club", "integer", required=True)),
    ),
)
section_spec = ServiceSpec(
    service=add_section_edition,
    permissions=[Unrestricted()],
    validator=FormValidator(NumberForm),
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=book_by_section,
        reads=Parameters.of(Parameter("section", "integer", required=True)),
    ),
)

club_view = SpecFormView.as_view(
    spec=club_spec, route_kwargs=("club",), success_url=reverse_lazy("done")
)
section_view = SpecFormView.as_view(
    spec=section_spec, route_kwargs=("section",), success_url=reverse_lazy("done")
)


def add_to_an_area(request: HttpRequest, **route: Any) -> HttpResponse:
    view = club_view if "club" in route else section_view
    return view(request, **route)


# --8<-- [end:areas]


def done(request: HttpRequest) -> HttpResponse:
    return HttpResponse("Done.")


def retitle(*, instance: Book, data: dict[str, Any]) -> Book:
    return instance


def book_by_pk(*, pk: int) -> QuerySet[Book]:
    return Book.objects.filter(pk=pk)


retitle_spec = ServiceSpec(
    service=retitle,
    permissions=[Unrestricted()],
    validator=FormValidator(RetitleForm),
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=book_by_pk,
        reads=Parameters.of(Parameter("pk", "integer", required=True)),
    ),
)

popping_spec = ServiceSpec(
    service=retitle,
    permissions=[Unrestricted()],
    validator=FormValidator(PoppingForm),
    instance_selector_spec=retitle_spec.instance_selector_spec,
)

# --8<-- [start:urls]
urlpatterns = [
    path("clubs/<int:club>/add/", add_to_an_area),
    path("sections/<int:section>/add/", add_to_an_area),
    path("done/", done, name="done"),
]
# --8<-- [end:urls]
urlpatterns += [
    path(
        "books/<int:pk>/retitle/",
        SpecFormView.as_view(spec=retitle_spec, route_kwargs=("pk",), success_url="/done/"),
    ),
    path(
        "books/<int:pk>/popping/",
        SpecFormView.as_view(spec=popping_spec, route_kwargs=("pk",), success_url="/done/"),
    ),
    path("books/keep/", SpecFormSetView.as_view(spec=keep_books_spec, success_url="/done/")),
]
