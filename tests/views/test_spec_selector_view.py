"""``SpecSelectorView``: a selector spec served as a page, a list's rows or one row.

Assertions read ``response.context_data`` - what a template is handed - and,
for what the template draws, the rendered body. Most pages render the shipped
template, so a spec here declares an Output marking the row's label; a test
about the rows a template is handed sets ``display_rows=False`` and renders
``spec_selector_rows.html``, which asks each row ``available``.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any

import django_filters
import django_tables2 as tables
import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db.models import QuerySet
from django.http import Http404
from django.template import TemplateSyntaxError
from django_service_specs import (
    DEFAULT_PAGE_SIZE,
    ActionUnavailable,
    AdditionalInputRequired,
    Affordance,
    DataclassPresenter,
    DispatchError,
    FieldMarking,
    InvalidArguments,
    NotPermitted,
    OutputPage,
    Parameter,
    Parameters,
    PrincipalUnavailable,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
)

from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
from django_spec_html.http.utils import REFUSAL_STATUSES, refusal_outcome, refusal_status
from django_spec_html.types.outcome import Outcome
from django_spec_html.views.spec_selector_view import SpecSelectorView
from tests.dispatch.utils import (
    BOOKS_OPEN,
    NOT_ARCHIVED,
    OPEN,
    PK,
    TENANT_SEEDS,
    Record,
    Refuse,
    make_user,
    note_by_pk,
    notes_of,
)
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, SPEC_OR_SOURCE, signed_in

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
ROWS = "spec_selector_rows.html"
DOCUMENT = "<!DOCTYPE html>"


@dataclasses.dataclass
class NoteOut:
    pk: Annotated[int, FieldMarking.handle()]
    title: Annotated[str, FieldMarking.label()]


SHOWN = DataclassPresenter(NoteOut)

# Two operations a note's row may offer, with a condition of each kind between
# them: on the row (``Q``) and over the seeds (a callable).
RENAME = ServiceSpec(service=Record(), permissions=OPEN, affordances=[NOT_ARCHIVED, BOOKS_OPEN])
CLOSED = Affordance(code="closed", reason="Closed for the night.", when=lambda: False)
REOPEN = ServiceSpec(service=Record(), permissions=OPEN, affordances=[CLOSED])
OFFERED = {"rename": RENAME, "reopen": REOPEN}


class TitleFilters(django_filters.FilterSet):
    title = django_filters.CharFilter(lookup_expr="icontains")
    archived = django_filters.BooleanFilter()

    class Meta:
        model = Note
        fields: list[str] = []


def listing(**fields: Any) -> SelectorSpec:
    fields.setdefault("selector", notes_of)
    fields.setdefault("presenter", SHOWN)
    fields.setdefault("permissions", OPEN)
    return SelectorSpec(kind=SelectorKind.LIST, **fields)


def one(**fields: Any) -> SelectorSpec:
    fields.setdefault("selector", note_by_pk)
    fields.setdefault("presenter", SHOWN)
    fields.setdefault("permissions", OPEN)
    fields.setdefault("reads", PK)
    return SelectorSpec(kind=SelectorKind.RETRIEVE, **fields)


def served(spec: Any, query: str = "", *, user: Any, headers: Any = None, **initkwargs: Any) -> Any:
    view = SpecSelectorView.as_view(spec=spec, **initkwargs)
    request = signed_in(FACTORY.get(f"/notes/?{query}", **(headers or {})), user)
    return view(request)


def shown(
    spec: Any, query: str = "", *, user: Any, url_kwargs: Any = None, **initkwargs: Any
) -> Any:
    """``spec`` served for ``query``, rendered."""
    view = SpecSelectorView.as_view(spec=spec, **initkwargs)
    response = view(signed_in(FACTORY.get(f"/notes/?{query}"), user), **(url_kwargs or {}))
    response.render()
    return response


def titles(response: Any) -> list[str]:
    return [row.title for row in response.context_data["rows"]]


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def notes(ada: Any) -> list[Note]:
    return [Note.objects.create(owner=ada, title=f"note {n}") for n in range(1, 6)]


class TestAsView:
    def test_a_write_is_refused_and_pointed_at_the_form_pages(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecSelectorView.as_view(spec=RENAME)

        assert str(refused.value) == (
            "SpecSelectorView.as_view() needs a SelectorSpec, the read a page lists or shows; "
            "got ServiceSpec. A write is served by SpecFormView or SpecConfirmView."
        )

    def test_no_spec_is_refused_as_none(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"needs a SelectorSpec: expected a"):
            SpecSelectorView.as_view()

    @SPEC_OR_SOURCE
    def test_a_spec_or_a_source_compiling_to_one_is_served(
        self, served: Any, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(served(listing()), user=ada)

        assert response.status_code == 200
        assert titles(response) == [note.title for note in notes]
        assert isinstance(response.context_data["spec"], SelectorSpec)

    def test_an_async_hook_is_refused_by_the_check_every_view_makes(self) -> None:
        class Seeded(SpecSelectorView):
            async def get_pool_seeds(self, request: Any) -> Any:  # ty: ignore[invalid-method-override]
                return TENANT_SEEDS

        with pytest.raises(ImproperlyConfigured, match=r"Seeded.get_pool_seeds is async"):
            Seeded.as_view(spec=listing())

    @pytest.mark.parametrize("key", ["", 3], ids=["empty", "a-number"])
    def test_a_page_kwarg_that_is_no_key_is_refused(self, key: Any) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecSelectorView.as_view(spec=listing(), page_kwarg=key)

        assert str(refused.value) == (
            f"SpecSelectorView.page_kwarg must be the query key naming the page; got {key!r}."
        )

    @pytest.mark.parametrize("key", ["", 3, "page"], ids=["empty", "a-number", "the-page-key"])
    def test_a_page_size_kwarg_that_is_no_key_is_refused(self, key: Any) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecSelectorView.as_view(spec=listing(), page_size_kwarg=key, max_page_size=10)

        assert str(refused.value) == (
            "SpecSelectorView.page_size_kwarg must be None or a query key other than "
            f"page_kwarg; got {key!r}."
        )

    @pytest.mark.parametrize("name", ["paginate_by", "max_page_size"])
    @pytest.mark.parametrize("size", ["10", True, 0], ids=["a-string", "true", "zero"])
    def test_a_size_that_is_no_positive_int_is_refused(self, name: str, size: Any) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecSelectorView.as_view(spec=listing(), **{name: size})

        assert str(refused.value) == (
            f"SpecSelectorView.{name} must be None or a positive int; got {size!r}."
        )

    def test_a_client_page_size_with_no_ceiling_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"set max_page_size\.$"):
            SpecSelectorView.as_view(spec=listing(), page_size_kwarg="per")

    @pytest.mark.parametrize(
        ("declared", "initkwargs"),
        [
            ("page", {}),
            ("per", {"page_size_kwarg": "per", "max_page_size": 10}),
        ],
        ids=["the-page-key", "the-page-size-key"],
    )
    def test_a_list_declaring_a_paging_key_is_refused(
        self, declared: str, initkwargs: dict[str, Any]
    ) -> None:
        spec = listing(reads=Parameters.of(Parameter(declared, "integer")))

        with pytest.raises(ImproperlyConfigured) as refused:
            SpecSelectorView.as_view(spec=spec, **initkwargs)

        assert str(refused.value).startswith(
            f"SpecSelectorView pages by {declared}, which the spec declares as a parameter"
        )

    def test_a_paging_key_renamed_away_from_the_parameter_is_served(
        self, ada: Any, notes: list[Note]
    ) -> None:
        def page_of(*, user: Any, page: int = 0) -> QuerySet[Note]:
            return Note.objects.filter(owner=user, pk__gt=page)

        spec = listing(selector=page_of, reads=Parameters.of(Parameter("page", "integer")))

        response = shown(spec, f"page={notes[2].pk}&p=1", user=ada, page_kwarg="p")

        assert titles(response) == ["note 4", "note 5"]

    def test_a_retrieve_is_not_paged_so_its_parameters_are_its_own(
        self, ada: Any, notes: list[Note]
    ) -> None:
        spec = one(
            selector=lambda *, page: Note.objects.filter(pk=page),
            reads=Parameters.of(Parameter("page", "integer", required=True)),
        )

        response = shown(spec, f"page={notes[1].pk}", user=ada)

        assert response.context_data["row"].title == "note 2"

    def test_a_table_order_by_field_the_spec_does_not_declare_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecSelectorView.as_view(spec=listing(), table_class=object, table_order_by_field="o")

        assert str(refused.value) == (
            "SpecSelectorView.table_order_by_field is 'o', so every header link sends 'o', "
            "which the spec does not declare: dispatch would ignore it, or refuse it under "
            "REJECT, and no header would sort the rows. Name the spec's own ordering "
            "parameter, such as its filter set's OrderingFilter, less any prefix the table's "
            "Meta sets."
        )

    def test_a_prefixed_table_s_order_field_is_read_with_its_prefix(self) -> None:
        # django-tables2 links a header as ``<Meta.prefix><order_by_field>``,
        # so a prefixed table's links would each be refused as an unknown
        # argument; the spec's ordering parameter does not carry the prefix.
        class Prefixed(tables.Table):
            title = tables.Column()

            class Meta:
                prefix = "t-"

        with pytest.raises(ImproperlyConfigured) as refused:
            SpecSelectorView.as_view(
                spec=listing(filter_set=Ordered), table_class=Prefixed, table_order_by_field="o"
            )

        assert "every header link sends 't-o'" in str(refused.value)


class TestTheRowsDisplay:
    def test_a_spec_with_no_output_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecSelectorView.as_view(spec=listing(presenter=None))

        assert str(refused.value) == (
            "SpecSelectorView displays its rows through its spec's Output, and the selector "
            "spec of notes_of declares none: give it a presenter, or set table_class, or "
            "display_rows = False for a template of the project's own."
        )

    def test_an_output_the_projection_refuses_is_refused_naming_the_view(self) -> None:
        @dataclasses.dataclass
        class TwoLabels:
            title: Annotated[str, FieldMarking.label()]
            owner: Annotated[str, FieldMarking.label()]

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^SpecSelectorView cannot display the rows of the selector spec of notes_of: ",
        ):
            SpecSelectorView.as_view(spec=listing(presenter=DataclassPresenter(TwoLabels)))

    def test_an_output_marking_none_of_its_fields_is_refused(self) -> None:
        @dataclasses.dataclass
        class Unmarked:
            title: str

        with pytest.raises(
            ImproperlyConfigured,
            match=r"display_rows = False for a template of the project's own\.$",
        ):
            SpecSelectorView.as_view(spec=listing(presenter=DataclassPresenter(Unmarked)))

    def test_display_rows_off_needs_no_output(self, ada: Any, notes: list[Note]) -> None:
        response = shown(
            listing(presenter=None),
            user=ada,
            display_rows=False,
            template_name="spec_selector_titles.html",
        )

        assert "row_displays" not in response.context_data
        assert titles(response) == [note.title for note in notes]

    def test_a_table_needs_no_output(self) -> None:
        SpecSelectorView.as_view(spec=listing(presenter=None), table_class=list)

    def test_each_row_is_shown_by_its_label_beside_the_row_itself(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(), user=ada)

        displays = response.context_data["row_displays"]
        assert [each["label"] for each in displays] == [note.title for note in notes]
        assert [each["row"] for each in displays] == response.context_data["rows"]
        # The handle is another call's input, and never read out to a person.
        assert [dict(each["pairs"]) for each in displays][0] == {"title": "note 1"}
        assert "<li>note 1</li>" in response.content.decode()

    def test_the_presenter_is_handed_the_context_dispatch_built(
        self, ada: Any, notes: list[Note]
    ) -> None:
        seen: list[Any] = []

        class Seeing(DataclassPresenter):
            def presented_rows(self, rows: Any, context: Any = None) -> list[Any]:
                seen.append(context)
                return super().presented_rows(rows, context)

        shown(listing(presenter=Seeing(NoteOut)), user=ada)

        (context,) = seen
        assert context.principal == ada
        assert context.call_context["view"].__class__ is SpecSelectorView


class TestTheListPage:
    def test_the_page_is_the_rows_and_how_to_ask_for_the_next(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(), user=ada, paginate_by=2)

        page = response.context_data["page"]
        assert isinstance(page, OutputPage)
        assert (page.page, page.limit, page.total, page.total_pages, page.has_next) == (
            1,
            2,
            5,
            3,
            True,
        )
        assert titles(response) == ["note 1", "note 2"]
        assert "row" not in response.context_data

    def test_page_items_are_the_rows(self, ada: Any, notes: list[Note]) -> None:
        response = shown(listing(), user=ada)

        assert response.context_data["page"].items is response.context_data["rows"]

    # Under REJECT a page key that reached the spec would be refused; under
    # IGNORE it would be dropped and the page would be page 1.
    @pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT])
    def test_the_page_key_never_reaches_the_spec(
        self, ada: Any, notes: list[Note], policy: UnknownArguments
    ) -> None:
        response = shown(listing(), "page=2", user=ada, paginate_by=2, unknown_arguments=policy)

        assert response.status_code == 200
        assert titles(response) == ["note 3", "note 4"]

    def test_the_request_s_own_query_is_left_as_it_came(self, ada: Any, notes: list[Note]) -> None:
        view = SpecSelectorView.as_view(spec=listing(), paginate_by=2)
        request = signed_in(FACTORY.get("/notes/?page=2"), ada)

        view(request)

        assert request.GET.dict() == {"page": "2"}

    @pytest.mark.parametrize(
        ("asked", "served_page"),
        [("x", 1), ("", 1), ("-4", 1), ("99", 3), ("2.5", 1)],
        ids=["a-word", "blank", "negative", "past-the-last", "a-fraction"],
    )
    def test_a_page_that_is_not_a_number_is_page_one(
        self, ada: Any, notes: list[Note], asked: str, served_page: int
    ) -> None:
        response = shown(listing(), f"page={asked}", user=ada, paginate_by=2)

        assert response.status_code == 200
        assert response.context_data["page"].page == served_page

    def test_the_kernel_s_default_size_with_no_paginate_by(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(), user=ada)

        assert response.context_data["page"].limit == DEFAULT_PAGE_SIZE

    def test_max_page_size_bounds_paginate_by(self, ada: Any, notes: list[Note]) -> None:
        response = shown(listing(), user=ada, paginate_by=4, max_page_size=3)

        assert response.context_data["page"].limit == 3

    def test_the_links_keep_every_filter_and_replace_the_page(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(
            listing(filter_set=TitleFilters), "title=note&page=2", user=ada, paginate_by=2
        )

        assert response.context_data["previous_page_query"] == "title=note&page=1"
        assert response.context_data["next_page_query"] == "title=note&page=3"
        body = response.content.decode()
        assert 'href="?title=note&amp;page=3" rel="next"' in body
        assert "Page 2 of 3" in body

    def test_the_first_and_last_pages_link_one_way(self, ada: Any, notes: list[Note]) -> None:
        first = shown(listing(), user=ada, paginate_by=5)

        assert "previous_page_query" not in first.context_data
        assert "next_page_query" not in first.context_data

    def test_a_link_from_a_clamped_page_leads_where_it_says(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(), "page=99", user=ada, paginate_by=2)

        assert response.context_data["previous_page_query"] == "page=2"

    def test_an_empty_list_says_so(self, ada: Any) -> None:
        response = shown(listing(), user=ada)

        assert response.context_data["rows"] == []
        assert "Nothing matches." in response.content.decode()


class TestAClientPageSize:
    @pytest.mark.parametrize(
        ("asked", "limit"),
        [("1", 1), ("50", 3), ("x", 2), ("", 2)],
        ids=["chosen", "over-the-ceiling", "not-a-number", "blank"],
    )
    @pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT])
    def test_it_is_read_and_bounded(
        self, ada: Any, notes: list[Note], asked: str, limit: int, policy: UnknownArguments
    ) -> None:
        response = shown(
            listing(),
            f"per={asked}",
            user=ada,
            page_size_kwarg="per",
            paginate_by=2,
            max_page_size=3,
            unknown_arguments=policy,
        )

        assert response.status_code == 200
        assert response.context_data["page"].limit == limit

    def test_without_a_key_a_client_s_size_is_an_argument_like_any_other(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(), "per=1", user=ada, unknown_arguments=UnknownArguments.REJECT)

        assert response.status_code == 400
        assert response.context_data["errors"] == ["per: Unknown argument."]

    def test_the_links_keep_it(self, ada: Any, notes: list[Note]) -> None:
        response = shown(listing(), "per=2", user=ada, page_size_kwarg="per", max_page_size=3)

        assert response.context_data["next_page_query"] == "per=2&page=2"


class TestTheInstancePage:
    def test_the_row_is_the_page_s_one_row_and_nothing_is_paged(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(one(), user=ada, url_kwargs={"pk": notes[0].pk})

        assert response.context_data["row"] == notes[0]
        assert response.context_data["rows"] == [notes[0]]
        assert "page" not in response.context_data
        assert "next_page_query" not in response.context_data
        assert "<li>note 1</li>" in response.content.decode()

    def test_a_missing_row_is_404(self, ada: Any) -> None:
        view = SpecSelectorView.as_view(spec=one())

        with pytest.raises(Http404):
            view(signed_in(FACTORY.get("/"), ada), pk=404)

    def test_a_missing_row_allow_none_lets_through_is_no_row(self, ada: Any) -> None:
        response = shown(one(allow_none=True), user=ada, url_kwargs={"pk": 404})

        assert response.status_code == 200
        assert response.context_data["rows"] == []
        assert "row" not in response.context_data
        assert "Nothing matches." in response.content.decode()

    def test_its_query_is_not_paged_so_a_page_key_is_refused_as_any_other(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(
            one(),
            "page=2",
            user=ada,
            url_kwargs={"pk": notes[0].pk},
            unknown_arguments=UnknownArguments.REJECT,
        )

        assert response.status_code == 400
        assert response.context_data["errors"] == ["page: Unknown argument."]

    def test_a_route_value_the_reads_refuse_is_400(self, ada: Any) -> None:
        response = shown(one(), user=ada, url_kwargs={"pk": "abc"})

        assert response.status_code == 400
        assert response.context_data["errors"] == ["pk: Enter a whole number."]


class TestTheRequest:
    def test_head_is_answered_and_a_post_is_not(self, ada: Any, notes: list[Note]) -> None:
        view = SpecSelectorView.as_view(spec=listing())

        assert view(signed_in(FACTORY.head("/"), ada)).status_code == 200
        refused = view(signed_in(FACTORY.post("/"), ada))
        assert refused.status_code == 405
        assert refused["Allow"] == "GET, HEAD"

    def test_a_principal_the_class_check_refuses_is_denied(self, ada: Any) -> None:
        view = SpecSelectorView.as_view(spec=listing(permissions=[Refuse()]))

        with pytest.raises(PermissionDenied, match="Only editors may run this."):
            view(signed_in(FACTORY.get("/"), ada))

    def test_the_class_check_is_asked_once(self, ada: Any, notes: list[Note]) -> None:
        asked: list[Any] = []

        class Counted(Refuse):
            def has_permission(self, principal: Any, spec: Any) -> bool:
                asked.append(principal)
                return True

        shown(listing(permissions=[Counted()]), user=ada)

        assert asked == [ada]

    def test_the_request_and_the_view_reach_the_selector(self, ada: Any, notes: list[Note]) -> None:
        handed: list[Any] = []

        def recorded(*, user: Any, request: Any, view: Any) -> QuerySet[Note]:
            handed.append((request, view))
            return Note.objects.filter(owner=user)

        view = SpecSelectorView.as_view(spec=listing(selector=recorded))
        request = signed_in(FACTORY.get("/notes/?page=1"), ada)
        view(request)

        ((seen_request, seen_view),) = handed
        assert seen_request is request
        assert isinstance(seen_view, SpecSelectorView)

    def test_a_seed_the_request_adds_reaches_the_selector(
        self, ada: Any, notes: list[Note]
    ) -> None:
        def scoped(*, user: Any, tenant: str) -> QuerySet[Note]:
            return Note.objects.filter(owner=user, title=f"note for {tenant}")

        class Tenanted(SpecSelectorView):
            def get_pool_seeds(self, request: Any) -> Any:
                return TENANT_SEEDS

        Note.objects.create(owner=ada, title="note for tenant-of-ada")

        response = shown_by(Tenanted, listing(selector=scoped), user=ada)

        assert titles(response) == ["note for tenant-of-ada"]

    def test_the_server_s_binding_outranks_the_client(self, ada: Any, notes: list[Note]) -> None:
        def titled(*, user: Any, title: str) -> QuerySet[Note]:
            return Note.objects.filter(owner=user, title=title)

        spec = listing(selector=titled, reads=Parameters.of(Parameter("title", "string")))
        response = shown(
            spec,
            "title=note%201",
            user=ada,
            server_arguments=ServerArguments(values={"title": "note 2"}),
        )

        assert titles(response) == ["note 2"]

    @pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT])
    def test_a_route_capture_the_spec_does_not_declare_is_refused(
        self, ada: Any, policy: UnknownArguments
    ) -> None:
        # The route is the host's, so the policy, which governs what a
        # client sends, does not reach it.
        view = SpecSelectorView.as_view(spec=listing(), unknown_arguments=policy)

        with pytest.raises(ImproperlyConfigured, match=r"pk"):
            view(signed_in(FACTORY.get("/"), ada), pk=1)


def shown_by(view_class: type[SpecSelectorView], spec: Any, *, user: Any, **initkwargs: Any) -> Any:
    response = view_class.as_view(spec=spec, **initkwargs)(signed_in(FACTORY.get("/"), user))
    response.render()
    return response


class TestFilters:
    def test_the_filter_set_is_bound_to_the_query_for_display(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(filter_set=TitleFilters), "title=3&page=1", user=ada)

        filters = response.context_data["filter"]
        assert isinstance(filters, TitleFilters)
        assert filters.form["title"].value() == "3"
        # The page key is the page's, never a filter's.
        assert "page" not in filters.data
        assert titles(response) == ["note 3"]
        body = response.content.decode()
        assert 'name="title" value="3"' in body

    def test_no_filter_set_puts_no_filter_in_the_context(self, ada: Any, notes: list[Note]) -> None:
        response = shown(listing(), user=ada)

        assert "filter" not in response.context_data
        assert "<form" not in response.content.decode()

    def test_a_refused_filter_is_placed_on_its_field_at_400(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(filter_set=TitleFilters), "archived=maybe", user=ada)

        assert response.status_code == 400
        form = response.context_data["filter"].form
        assert form.errors == {"archived": ["Enter true, false, 1 or 0."]}
        assert response.context_data["errors"] == ["archived: Enter true, false, 1 or 0."]
        assert "rows" not in response.context_data
        body = response.content.decode()
        assert "<li>archived: Enter true, false, 1 or 0.</li>" in body
        # The page's form shows the field, and its message once, in the results.
        assert body.count("Enter true, false, 1 or 0.") == 1

    def test_an_undeclared_key_is_placed_on_the_form_s_non_field_errors(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(
            listing(filter_set=TitleFilters),
            "colour=red",
            user=ada,
            unknown_arguments=UnknownArguments.REJECT,
        )

        assert response.status_code == 400
        assert response.context_data["filter"].form.non_field_errors() == [
            "colour: Unknown argument."
        ]

    def test_the_form_s_own_validation_is_never_run(self, ada: Any, notes: list[Note]) -> None:
        cleaned: list[str] = []

        class Counting(forms.Form):
            def clean(self) -> dict[str, Any]:
                cleaned.append("clean")
                return super().clean()

        class Counted(TitleFilters):
            class Meta(TitleFilters.Meta):
                form = Counting

        response = shown(listing(filter_set=Counted), "title=note", user=ada)

        # Once, by dispatch reading the filter set; the form the page shows
        # is rendered with no error state, so drawing a field asks nothing.
        assert cleaned == ["clean"]
        assert response.context_data["filter"].form.errors == {}

    def test_the_spec_s_filter_form_decides_and_its_refusal_is_shown(
        self, ada: Any, notes: list[Note]
    ) -> None:
        class Refusing(forms.Form):
            def clean(self) -> dict[str, Any]:
                raise ValidationError("Narrow the search.")

        class Strict(TitleFilters):
            class Meta(TitleFilters.Meta):
                form = Refusing

        response = shown(listing(filter_set=Strict), "title=note", user=ada)

        assert response.status_code == 400
        assert response.context_data["filter"].form.non_field_errors() == ["Narrow the search."]
        assert response.content.decode().count("Narrow the search.") == 1

    def test_an_undeclared_key_is_ignored_by_default(self, ada: Any, notes: list[Note]) -> None:
        # A tracking parameter on a link the page does not control.
        response = shown(listing(filter_set=TitleFilters), "utm_source=x", user=ada)

        assert response.status_code == 200
        assert titles(response) == [note.title for note in notes]
        assert "errors" not in response.context_data
        assert response.context_data["filter"].form.errors == {}

    def test_an_undeclared_key_beside_a_filter_leaves_the_filter_read(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(filter_set=TitleFilters), "title=3&utm_source=x", user=ada)

        assert response.status_code == 200
        assert titles(response) == ["note 3"]

    def test_an_undeclared_key_on_an_instance_page_is_ignored_by_default(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(one(), "utm_source=x", user=ada, url_kwargs={"pk": notes[0].pk})

        assert response.status_code == 200
        assert response.context_data["row"] == notes[0]

    def test_a_declared_key_s_bad_value_is_still_refused_by_default(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(filter_set=TitleFilters), "archived=maybe&utm_source=x", user=ada)

        assert response.status_code == 400
        assert response.context_data["errors"] == ["archived: Enter true, false, 1 or 0."]
        assert "rows" not in response.context_data

    def test_reject_set_on_the_view_refuses_an_undeclared_key(
        self, ada: Any, notes: list[Note]
    ) -> None:
        class Strict(SpecSelectorView):
            unknown_arguments = UnknownArguments.REJECT

        view = Strict.as_view(spec=listing())
        response = view(signed_in(FACTORY.get("/notes/?utm_source=x"), ada))

        assert response.status_code == 400
        assert response.context_data["errors"] == ["utm_source: Unknown argument."]

    def test_the_default_is_ignore(self) -> None:
        assert SpecSelectorView.unknown_arguments is UnknownArguments.IGNORE


class TestTheFragment:
    def test_an_htmx_request_is_answered_with_the_results_alone(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = served(listing(), user=ada, headers=HTMX)
        response.render()

        body = response.content.decode()
        assert DOCUMENT not in body
        assert "<li>note 1</li>" in body
        assert response["Vary"] == "HX-Request, HX-Boosted, HX-History-Restore-Request"

    def test_an_undeclared_key_is_ignored_by_the_fragment_too(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = served(listing(), "utm_source=x", user=ada, headers=HTMX)
        response.render()

        body = response.content.decode()
        assert response.status_code == 200
        assert response.template_name == ["django_spec_html/spec_selector_fragment.html"]
        assert DOCUMENT not in body
        assert titles(response) == [note.title for note in notes]
        assert "Unknown argument." not in body

    def test_the_page_carries_the_fragment_inside_its_results(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(), user=ada)

        body = response.content.decode()
        assert body.startswith(DOCUMENT)
        assert '<div id="spec-results">' in body
        assert response["Vary"] == "HX-Request, HX-Boosted, HX-History-Restore-Request"

    def test_a_refusal_is_the_fragment_at_200(self, ada: Any, notes: list[Note]) -> None:
        response = served(
            listing(),
            "colour=red",
            user=ada,
            headers=HTMX,
            unknown_arguments=UnknownArguments.REJECT,
        )
        response.render()

        assert response.status_code == 200
        assert "colour: Unknown argument." in response.content.decode()

    def test_a_template_of_the_project_s_own_is_answered_by_its_spec_results(self) -> None:
        view = SpecSelectorView(template_name="mine.html")

        assert view.get_fragment_template_names() == ["mine.html#spec-results"]


def raising(refusal: Exception) -> SelectorSpec:
    def selector() -> Any:
        raise refusal

    return listing(selector=selector)


EXAMPLES: list[Any] = [
    InvalidArguments({"title": ["Too short."]}),
    NotPermitted(),
    PrincipalUnavailable(),
    UnsupportedMediaType(),
    DispatchError("The read could not run."),
    ServiceValidationError("Not a valid search."),
    ServiceNotFound(),
    ActionUnavailable(code="closed", message="Closed for the night."),
    ServiceConflict("Busy."),
    AdditionalInputRequired("Which shelf?", schema={"shelf": {"type": "string"}}),
    ServiceError("The shelf is locked."),
]


def test_the_examples_cover_every_row_of_the_map() -> None:
    assert [type(each) for each in EXAMPLES] == [kind for kind, _, _ in REFUSAL_STATUSES]


@pytest.mark.parametrize("refusal", EXAMPLES, ids=lambda each: type(each).__name__)
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "fragment"])
def test_every_refusal_is_answered_from_the_one_map(
    ada: Any, refusal: Exception, headers: dict[str, str]
) -> None:
    view = SpecSelectorView.as_view(spec=raising(refusal))
    request = signed_in(FACTORY.get("/", **headers), ada)
    assert isinstance(refusal, DispatchError | ServiceError)

    if refusal_outcome(refusal) is Outcome.DENIED:
        expected = PermissionDenied if refusal_status(refusal) == 403 else Http404
        with pytest.raises(expected):
            view(request)
        return
    response = view(request)
    response.render()

    assert response.status_code == (200 if headers else refusal_status(refusal))
    assert response.context_data["errors"]
    for error in response.context_data["errors"]:
        assert error in response.content.decode()


def test_a_refusal_carrying_a_code_says_it(ada: Any) -> None:
    response = shown(raising(ActionUnavailable(code="closed", message="Closed.")), user=ada)

    assert response.status_code == 409
    assert response.context_data["code"] == "closed"
    assert 'data-code="closed"' in response.content.decode()


class TestAffordances:
    def test_each_row_answers_what_dispatch_would_enforce(self, ada: Any) -> None:
        Note.objects.create(owner=ada, title="live")
        Note.objects.create(owner=ada, title="archived", archived=True)

        response = shown(
            listing(affordances=OFFERED), user=ada, display_rows=False, template_name=ROWS
        )

        assert response.content.decode().split() == [
            "live:rename,-reopen",
            "archived:-rename,-reopen",
        ]

    def test_the_instance_page_s_row_answers_too(self, ada: Any) -> None:
        archived = Note.objects.create(owner=ada, title="archived", archived=True)

        response = shown(
            one(affordances=OFFERED),
            user=ada,
            url_kwargs={"pk": archived.pk},
            display_rows=False,
            template_name=ROWS,
        )

        assert response.content.decode().split() == ["archived:-rename,-reopen"]

    def test_a_name_the_spec_does_not_declare_raises_naming_those_it_does(
        self, ada: Any, notes: list[Note]
    ) -> None:
        with pytest.raises(TemplateSyntaxError) as refused:
            shown(
                listing(affordances=OFFERED),
                user=ada,
                display_rows=False,
                template_name="spec_selector_misspelt.html",
            )

        assert str(refused.value) == (
            "available was asked about 'renmae', which this row's spec does not declare among "
            "its affordances; it declares 'rename', 'reopen'."
        )

    def test_a_spec_declaring_none_raises_for_any_name(self, ada: Any, notes: list[Note]) -> None:
        with pytest.raises(TemplateSyntaxError, match=r"it declares none\.$"):
            shown(listing(), user=ada, display_rows=False, template_name=ROWS)

    def test_a_mapping_row_is_copied_and_the_selector_s_own_left_alone(self, ada: Any) -> None:
        constant = [{"title": "live"}]
        callable_only = {"reopen": REOPEN}
        spec = listing(selector=lambda: constant, presenter=None, affordances=callable_only)

        response = shown(
            spec, user=ada, display_rows=False, template_name="spec_selector_dicts.html"
        )

        assert response.content.decode().split() == ["live:-reopen"]
        assert constant == [{"title": "live"}]
        assert response.context_data["rows"] == [
            {"title": "live", "affordance__reopen__closed": False}
        ]

    def test_a_values_row_is_answered_from_its_annotations(self, ada: Any) -> None:
        Note.objects.create(owner=ada, title="archived", archived=True)
        spec = listing(
            selector=lambda *, user: Note.objects.filter(owner=user).values("title"),
            presenter=None,
            affordances=OFFERED,
        )

        response = shown(
            spec, user=ada, display_rows=False, template_name="spec_selector_dicts.html"
        )

        assert response.content.decode().split() == ["archived:-reopen"]

    def test_a_row_that_takes_no_attribute_is_served_without_answers(self, ada: Any) -> None:
        spec = listing(selector=lambda: [("live",)], presenter=None)

        with pytest.raises(TemplateSyntaxError) as refused:
            shown(spec, user=ada, display_rows=False, template_name="spec_selector_dicts.html")

        assert response_rows(spec, ada) == [("live",)]
        assert str(refused.value).startswith(
            "available was asked about a tuple that carries no affordance answers"
        )


def response_rows(spec: Any, user: Any) -> Any:
    view = SpecSelectorView.as_view(spec=spec, display_rows=False, template_name=ROWS)
    return view(signed_in(FACTORY.get("/"), user)).context_data["rows"]


class Ordered(TitleFilters):
    o = django_filters.OrderingFilter(fields=("title",))


ACTIONS = (
    '{% load django_spec_html %}{% if record|available:"rename" %}'
    "<button>Rename</button>{% else %}<span>locked</span>{% endif %}"
)


class NoteTable(tables.Table):
    title = tables.Column()
    actions = tables.TemplateColumn(ACTIONS, orderable=False)

    class Meta:
        # Declared to show that it is never applied: the spec orders the rows.
        order_by = ("-title",)


@pytest.fixture
def with_tables2(settings: Any) -> None:
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "django_tables2"]


@pytest.mark.usefixtures("with_tables2")
class TestATable:
    def test_it_is_built_over_the_answered_rows(self, ada: Any, notes: list[Note]) -> None:
        response = shown(listing(affordances=OFFERED), user=ada, table_class=NoteTable)

        table = response.context_data["table"]
        assert isinstance(table, NoteTable)
        assert [row.record for row in table.rows] == response.context_data["rows"]
        assert "row_displays" not in response.context_data
        assert response.content.decode().count("<button>Rename</button>") == 5

    def test_a_table_s_own_order_by_never_reorders_the_rows(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(affordances=OFFERED), user=ada, table_class=NoteTable)

        table = response.context_data["table"]
        assert [row.record.title for row in table.rows] == [note.title for note in notes]

    def test_a_table_s_own_order_by_never_reorders_a_sorted_page(
        self, ada: Any, notes: list[Note]
    ) -> None:
        # NoteTable's Meta orders by -title. With headers that sort, tables2
        # would apply that order to the page the spec sorted by ?o=title,
        # unless the page builds the table with no ordering of its own.
        view = SpecSelectorView.as_view(
            spec=listing(filter_set=Ordered, affordances=OFFERED),
            table_class=NoteTable,
            table_order_by_field="o",
        )
        response = view(signed_in(FACTORY.get("/notes/?o=title"), ada))
        response.render()

        rows = response.context_data["table"].rows
        assert [row.record.title for row in rows] == sorted(note.title for note in notes)

    def test_with_no_order_field_no_header_links_a_sort(self, ada: Any, notes: list[Note]) -> None:
        response = shown(listing(affordances=OFFERED), user=ada, table_class=NoteTable)

        assert response.context_data["table"].orderable is False
        assert "?sort=" not in response.content.decode()

    def test_a_header_link_is_a_query_the_spec_reads(self, ada: Any, notes: list[Note]) -> None:
        view = SpecSelectorView.as_view(
            spec=listing(filter_set=Ordered, affordances=OFFERED),
            table_class=NoteTable,
            table_order_by_field="o",
            paginate_by=2,
        )
        first = view(signed_in(FACTORY.get("/notes/?page=2"), ada))
        first.render()
        assert 'href="?page=2&amp;o=title"' in first.content.decode()

        followed = view(signed_in(FACTORY.get("/notes/?page=1&o=-title"), ada))
        followed.render()

        assert followed.status_code == 200
        assert [row.record.title for row in followed.context_data["table"].rows] == [
            "note 5",
            "note 4",
        ]
        assert followed.context_data["next_page_query"] == "page=2&o=-title"

    def test_a_column_renders_without_the_page_s_context(self, ada: Any, notes: list[Note]) -> None:
        class Posting(tables.Table):
            title = tables.TemplateColumn("[{% csrf_token %}{{ view }}]")

        response = shown(listing(), user=ada, table_class=Posting)

        assert response.content.decode().count("[]") == 5
        assert "csrfmiddlewaretoken" not in response.content.decode()

    def test_a_shipped_page_includes_the_table_s_own_template(
        self, ada: Any, notes: list[Note]
    ) -> None:
        response = shown(listing(affordances=OFFERED), user=ada, table_class=NoteTable)

        body = response.content.decode()
        assert body.count("<table") == 1
        assert "{% load django_tables2 %}" not in body


def make_notes(owner: Any, count: int) -> None:
    Note.objects.bulk_create(
        Note(owner=owner, title=f"note {n}", archived=n % 2 == 0) for n in range(count)
    )


class OwnerFilters(TitleFilters):
    owner = django_filters.ModelChoiceFilter(queryset=get_user_model().objects.all())


# A COUNT, the page of rows with every affordance flag annotated on it, and the
# owner filter's choices as the page's form draws them. No query per row.
LIST_QUERIES = 3
PERF_SPEC = listing(filter_set=OwnerFilters, affordances=OFFERED)


class TestQueries:
    @pytest.mark.parametrize("count", [3, 12])
    def test_the_default_page_queries_the_same_for_any_number_of_rows(
        self, ada: Any, count: int, django_assert_num_queries: Any
    ) -> None:
        make_notes(ada, count)

        with django_assert_num_queries(LIST_QUERIES):
            response = shown(PERF_SPEC, user=ada)

        body = response.content.decode()
        assert body.count("<li>note ") == count
        assert '<option value="' in body

    @pytest.mark.usefixtures("with_tables2")
    @pytest.mark.parametrize("count", [3, 12])
    def test_a_table_asking_each_row_queries_the_same_for_any_number_of_rows(
        self, ada: Any, count: int, django_assert_num_queries: Any
    ) -> None:
        make_notes(ada, count)

        with django_assert_num_queries(LIST_QUERIES):
            response = shown(PERF_SPEC, user=ada, table_class=NoteTable)

        body = response.content.decode()
        assert body.count("<button>Rename</button>") + body.count("<span>locked</span>") == count
        assert '<option value="' in body

    def test_the_instance_page_answers_its_row_in_its_one_query(
        self, ada: Any, django_assert_num_queries: Any
    ) -> None:
        note = Note.objects.create(owner=ada, title="live")

        with django_assert_num_queries(1):
            response = shown(
                one(affordances=OFFERED),
                user=ada,
                url_kwargs={"pk": note.pk},
                template_name=ROWS,
                display_rows=False,
            )

        assert response.content.decode().split() == ["live:rename,-reopen"]
