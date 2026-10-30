"""Serving a read: a selector spec as a list page, a table and an instance page.

The module is a whole URLconf over the shipped templates, so it serves real
pages: set ``ROOT_URLCONF = "docs.examples.spec_selector"``, add
``django_tables2`` and ``tests.adapter_app`` to ``INSTALLED_APPS`` beside
``django_spec_html``, run ``migrate --run-syncdb`` (the test app ships no
migrations), call ``seed()`` once, and open ``/books/``. ``tests/docs/test_spec_selector_examples.py``
requests each page as a browser would and follows the links it renders, so
``docs/selectors.md`` cannot describe a page, a link or a query count the
view does not make.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Annotated

import django_filters
import django_tables2 as tables
from django.db.models import Q, QuerySet
from django.http import HttpRequest, QueryDict
from django.urls import path, reverse_lazy
from django_service_specs import (
    Affordance,
    DataclassPresenter,
    FieldMarking,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_html import SpecConfirmView, SpecSelectorView
from tests.adapter_app.models import Author, Book, Status

# The switch the second condition reads, so a reader can watch every row's
# button go at once: a condition over the seeds, asked once per page.
WITHDRAWALS_PAUSED = False

OPEN = [Unrestricted()]
BY_PK = Parameters.of(Parameter("pk", "integer", required=True))


# --8<-- [start:spec]
@dataclasses.dataclass
class BookOut:
    """A book as a page names it."""

    pk: Annotated[int, FieldMarking.handle()]  # another call's input: never shown
    title: Annotated[str, FieldMarking.label()]  # the name a page calls it by
    status: Status


def book_by_pk(*, pk: int) -> QuerySet[Book]:
    return Book.objects.filter(pk=pk)


def withdraw(*, instance: Book) -> Book:
    instance.status = Status.DRAFT
    instance.save(update_fields=["status"])
    return instance


def withdrawals_open() -> bool:
    return not WITHDRAWALS_PAUSED


# The operation a row offers, with a condition of each kind: one on the row,
# answered for every row in the page's one query, and one over the seeds.
withdraw_book_spec = ServiceSpec(
    service=withdraw,
    permissions=OPEN,
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=book_by_pk, reads=BY_PK
    ),
    presenter=DataclassPresenter(BookOut),
    affordances=[
        Affordance(
            code="not_published",
            reason="Only a published book can be withdrawn.",
            when=Q(status=Status.PUBLISHED),
        ),
        Affordance(
            code="withdrawals_paused",
            reason="Withdrawals are paused.",
            when=withdrawals_open,
        ),
    ],
)


class BookFilters(django_filters.FilterSet):
    title = django_filters.CharFilter(lookup_expr="icontains")
    author = django_filters.ModelChoiceFilter(queryset=Author.objects.order_by("name"))
    o = django_filters.OrderingFilter(fields=("title", "price"))

    class Meta:
        model = Book
        fields: list[str] = []


def books() -> QuerySet[Book]:
    # The author joined in, since the table shows each row's author's name.
    return Book.objects.select_related("author")


book_list_spec = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=books,
    permissions=OPEN,
    filter_set=BookFilters,
    presenter=DataclassPresenter(BookOut),
    affordances={"withdraw": withdraw_book_spec},
)

book_spec = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    selector=book_by_pk,
    reads=BY_PK,
    permissions=OPEN,
    presenter=DataclassPresenter(BookOut),
    affordances={"withdraw": withdraw_book_spec},
)
# --8<-- [end:spec]


# --8<-- [start:table]
class BookTable(tables.Table):
    title = tables.Column()
    # Not among the OrderingFilter's fields, so its header must not link a
    # sort: dispatch would refuse ``o=author``, and the link would answer 400.
    author = tables.Column(accessor="author__name", verbose_name="Author", orderable=False)
    price = tables.Column()
    withdraw = tables.TemplateColumn(
        '{% load django_spec_html %}{% if record|available:"withdraw" %}'
        '<a href="{% url "withdraw-book" record.pk %}">Withdraw</a>'
        "{% else %}<span>Not available</span>{% endif %}",
        orderable=False,
        verbose_name="",
    )


# --8<-- [end:table]


# --8<-- [start:urls]
urlpatterns = [
    path(
        "books/",
        SpecSelectorView.as_view(
            spec=book_list_spec,
            paginate_by=10,
            # A client may choose its page size, up to the ceiling.
            page_size_kwarg="per_page",
            max_page_size=50,
        ),
        name="books",
    ),
    path(
        "books/table/",
        SpecSelectorView.as_view(
            spec=book_list_spec,
            paginate_by=10,
            table_class=BookTable,
            # The filter set's OrderingFilter: each header link sends it.
            table_order_by_field="o",
        ),
        name="book-table",
    ),
    path("books/<int:pk>/", SpecSelectorView.as_view(spec=book_spec), name="book"),
    path(
        "books/<int:pk>/withdraw/",
        SpecConfirmView.as_view(
            spec=withdraw_book_spec,
            success_url=reverse_lazy("book-table"),
            route_kwargs=("pk",),
            display_target=True,
        ),
        name="withdraw-book",
    ),
]
# --8<-- [end:urls]


# --8<-- [start:remembered]
class RememberedBooks(SpecSelectorView):
    """The book list, opened where this session's last visit left it."""

    def get_query(self, request: HttpRequest) -> QueryDict:
        # A visit that sends anything is the query, and is remembered...
        if request.GET:
            request.session["books_query"] = request.GET.urlencode()
            return request.GET
        # ...and a bare visit reads the last one: its filters, sort and page.
        return QueryDict(request.session.get("books_query", ""))


urlpatterns += [
    path(
        "books/remembered/",
        RememberedBooks.as_view(spec=book_list_spec, paginate_by=10),
        name="remembered-books",
    ),
]
# --8<-- [end:remembered]


def seed(count: int = 25) -> list[Book]:
    """Two authors and ``count`` books, every third one a draft."""
    authors = [Author.objects.create(name=name) for name in ("Ursula", "Octavia")]
    return Book.objects.bulk_create(
        Book(
            author=authors[n % 2],
            title=f"Book {n:02d}",
            price=Decimal(10 + n),
            status=Status.DRAFT if n % 3 == 0 else Status.PUBLISHED,
        )
        for n in range(1, count + 1)
    )
