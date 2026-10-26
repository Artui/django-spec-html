"""``SpecFormSetView``: a ``many`` spec validated by a form, served as a formset of that form.

Assertions read ``response.context_data["formset"]`` - the bound formset the
page re-renders - and what the service received, rather than parsing HTML.
The Validator records the rows the kernel hands it, so ``rows(spec)`` is the
list the page sent under ``many_argument``, once the shape check had read it,
and the service is a ``Record``, so a call that ran is seen to have run.
The module is its own URLconf, since ``resolve_url`` reads the project's.
"""

from __future__ import annotations

import dataclasses
import types
from collections.abc import Callable, Mapping
from typing import Annotated, Any

import pytest
from django import forms
from django.contrib.messages import get_messages
from django.core.exceptions import BadRequest, ImproperlyConfigured, PermissionDenied
from django.http import HttpResponse
from django.test import Client
from django.urls import path
from django_service_specs import (
    NON_FIELD_ERRORS,
    AdditionalInputRequired,
    DataclassPresenter,
    DispatchResult,
    FieldMarking,
    FormValidator,
    InvalidArguments,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    ServiceConflict,
    ServiceSpec,
    ServiceValidationError,
    Validated,
    ValidationContext,
)

from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from django_spec_html.views.utils import SpecPageBase
from tests.adapter_app.models import Author
from tests.dispatch.utils import OPEN, Record, Refuse, Titled, make_user
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

TEMPLATE = "spec_formset.html"
FRAGMENT = "spec_formset_form.html"
HTMX = {"HTTP_HX_REQUEST": "true"}
REQUIRED = "This field is required."
TOO_LONG = "Ensure this value has at most 5 characters (it has 7)."
CLOSED = "Entries are closed."
UNLUCKY = "Thirteen is unlucky."
MANAGEMENT = "ManagementForm data is missing or has been tampered with."


class BookRow(forms.Form):
    """One row of a batch: the page's form, and the Validator of each row."""

    title = forms.CharField(max_length=5)
    urgent = forms.BooleanField(required=False)
    count = forms.IntegerField(required=False)
    due = forms.DateField(required=False)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("title") == "shut":
            raise forms.ValidationError(CLOSED)
        if cleaned.get("count") == 13:
            raise forms.ValidationError(UNLUCKY)
        return cleaned


class AuthoredRow(forms.Form):
    """A row choosing an author by key, so the row's value is a string the kernel types."""

    author = forms.ModelChoiceField(queryset=Author.objects.all())


class OwnedRow(forms.Form):
    """A row whose ``__init__`` takes a keyword, as a form scoped to its caller does."""

    title = forms.CharField()

    def __init__(self, *args: Any, owner: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.owner = owner


class Cleaned(forms.Form):
    """Counts each ``clean()``, on the class, across every instance a post builds."""

    cleans = 0
    title = forms.CharField()

    def clean(self) -> dict[str, Any]:
        type(self).cleans += 1
        return super().clean() or {}


class Watched(FormValidator):
    """``FormValidator``, recording each list of rows the kernel hands it: what the page sent, checked."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lists: list[list[dict[str, Any]]] = []

    def validated_many(
        self,
        rows: Mapping[int, Mapping[str, Any]],
        context: ValidationContext,
        *,
        many_argument: str,
    ) -> Validated:
        self.lists.append([dict(row) for row in rows.values()])
        return super().validated_many(rows, context, many_argument=many_argument)


class NoTwoAlike(Watched):
    """Refuses a batch whose titles repeat, as a message about the list as a whole."""

    def validated_many(
        self,
        rows: Mapping[int, Mapping[str, Any]],
        context: ValidationContext,
        *,
        many_argument: str,
    ) -> Validated:
        titles = [row["title"] for row in rows.values()]
        if len(set(titles)) < len(titles):
            raise InvalidArguments({NON_FIELD_ERRORS: ["Two rows share a title."]})
        return super().validated_many(rows, context, many_argument=many_argument)


class Scoped(PermissionCheck):
    """Admits every principal, then answers the scope check as told; records each scope it saw."""

    message = "No batch may be added here."

    def __init__(self, admits: bool = True) -> None:
        self.admits = admits
        self.scopes: list[Any] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_scope_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        self.scopes.append(target)
        return self.admits


@dataclasses.dataclass
class BookOut:
    title: Annotated[str, FieldMarking.label()]


def many_spec(form_class: type[forms.BaseForm] = BookRow, **kwargs: Any) -> ServiceSpec:
    """A batch write validated row by row by ``form_class``, whose service records what it received."""
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("validator", Watched(form_class))
    return ServiceSpec(service=Record(), many=True, **kwargs)


def formset_view(spec: ServiceSpec, **initkwargs: Any) -> Any:
    initkwargs.setdefault("template_name", TEMPLATE)
    initkwargs.setdefault("success_url", "/done/")
    return SpecFormSetView.as_view(spec=spec, **initkwargs)


def adding(spec: ServiceSpec, **attributes: Any) -> Any:
    """A page committing only from ``save``, and adding a form from ``add``, set as a host's subclass sets them."""
    attributes = {
        "template_name": TEMPLATE,
        "success_url": "/done/",
        "commit_button": "save",
        "add_button": "add",
        **attributes,
    }
    return type("Adding", (SpecFormSetView,), attributes).as_view(spec=spec)


def posted(*rows: Mapping[str, Any], total: int | None = None, **extra: str) -> dict[str, Any]:
    """What a browser posts for ``rows``: the management form, then each row's fields under its prefix."""
    data: dict[str, Any] = {
        "form-TOTAL_FORMS": str(len(rows) if total is None else total),
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
        **extra,
    }
    for index, row in enumerate(rows):
        data.update({f"form-{index}-{key}": value for key, value in row.items()})
    return data


def get(view: Any, user: Any, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user), **url_kwargs)


def post(view: Any, user: Any, data: Mapping[str, Any], **headers: Any) -> Any:
    url_kwargs = headers.pop("url_kwargs", {})
    return view(signed_in(FACTORY.post("/", data=data, **headers), user), **url_kwargs)


def rows(spec: ServiceSpec) -> list[dict[str, Any]]:
    """The rows the page sent, once, as the kernel handed them to the Validator, and the call ran."""
    assert len(spec.service.calls) == 1
    [sent] = spec.validator.lists
    return sent


def row_errors(response: Any) -> list[dict[str, list[str]]]:
    """Each re-rendered form's errors, as a person reads them, in the formset's order."""
    formset = response.context_data["formset"]
    assert formset.is_bound
    return [
        {key: list(messages) for key, messages in form.errors.items()} for form in formset.forms
    ]


def non_form(response: Any) -> list[str]:
    return list(response.context_data["formset"].non_form_errors())


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


class TestAsView:
    """``as_view()`` refuses, at URLconf import, a view that could not serve its first request."""

    def test_the_single_form_page_refuses_a_many_spec(self) -> None:
        # What this view exists for: the single page posts one row.
        with pytest.raises(ImproperlyConfigured, match="many spec"):
            SpecFormView.as_view(spec=many_spec(), template_name=TEMPLATE, success_url="/done/")

    def test_a_spec_that_is_not_many_is_refused(self) -> None:
        spec = ServiceSpec(service=Record(), permissions=OPEN, validator=FormValidator(BookRow))
        with pytest.raises(ImproperlyConfigured, match="its spec takes one"):
            formset_view(spec)

    def test_a_validator_that_is_not_a_form_validator_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be a FormValidator; got Titled"):
            formset_view(many_spec(validator=Titled()))

    def test_displaying_the_target_is_refused(self) -> None:
        # Sized so the base's display check passes: an Output marking a field.
        spec = many_spec(presenter=DataclassPresenter(BookOut))
        with pytest.raises(ImproperlyConfigured, match="no target to display"):
            formset_view(spec, display_target=True)

    @pytest.mark.parametrize("extra", ["1", None, True, -1], ids=["a-str", "none", "bool", "neg"])
    def test_anything_but_a_non_negative_int_is_refused(self, extra: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match="extra must be a non-negative int"):
            formset_view(many_spec(), extra=extra)

    def test_no_extra_form_is_a_page(self, ada: Any) -> None:
        response = get(formset_view(many_spec(), extra=0), ada)
        assert len(response.context_data["formset"].forms) == 0

    @pytest.mark.parametrize("add", [("add",), ""], ids=["a-tuple", "empty"])
    def test_anything_but_a_button_s_name_is_refused(self, add: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match="add_button must be None or the name"):
            formset_view(many_spec(), commit_button="save", add_button=add)

    def test_an_add_button_without_a_commit_button_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="add_button needs a commit_button"):
            formset_view(many_spec(), add_button="add")

    def test_the_commit_button_is_no_add_button(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="both name 'save'"):
            formset_view(many_spec(), commit_button="save", add_button="save")

    @pytest.mark.parametrize(
        ("attributes", "named"),
        [
            ({"commit_button": "form-TOTAL_FORMS"}, "commit_button"),
            ({"commit_button": "save", "add_button": "form-0-title"}, "add_button"),
        ],
        ids=["commit", "add"],
    )
    def test_a_button_under_the_formset_s_prefix_is_refused(
        self, attributes: dict[str, str], named: str
    ) -> None:
        with pytest.raises(ImproperlyConfigured, match=f"{named} names .* under the formset's"):
            formset_view(many_spec(), **attributes)


class TestGet:
    def test_renders_an_unbound_formset_of_extra_forms(self, ada: Any) -> None:
        spec = many_spec()
        response = get(formset_view(spec, extra=3), ada)
        assert response.status_code == 200
        formset = response.context_data["formset"]
        assert not formset.is_bound
        assert [type(form) for form in formset.forms] == [BookRow] * 3
        assert response.context_data["spec"] is spec
        assert b'name="form-TOTAL_FORMS"' in response.render().content

    @pytest.mark.parametrize(
        ("bounds", "extra", "count"),
        [({"many_min_length": 2}, 1, 3), ({"many_max_length": 2}, 5, 2)],
        ids=["min-and-extra", "never-past-max"],
    )
    def test_the_spec_s_list_bounds_the_formset(
        self, ada: Any, bounds: dict[str, int], extra: int, count: int
    ) -> None:
        response = get(formset_view(many_spec(**bounds), extra=extra), ada)
        assert len(response.context_data["formset"].forms) == count

    def test_a_many_page_renders_no_target_pane(self, ada: Any) -> None:
        # On GET and on a refused post alike: a many spec has no target.
        view = formset_view(many_spec())
        on_get = get(view, ada).context_data
        on_post = post(view, ada, posted({"title": "toolong"})).context_data
        for context in (on_get, on_post):
            assert not {key for key in context if key.startswith("target")}

    def test_each_form_is_built_with_what_form_kwargs_answers(self, ada: Any) -> None:
        spec = many_spec(validator=Watched(OwnedRow, form_kwargs=lambda *, user: {"owner": user}))
        view = formset_view(spec, extra=2)
        assert [form.owner for form in get(view, ada).context_data["formset"].forms] == [ada, ada]
        response = post(view, ada, posted({"title": "One"}, {"title": "Two"}))
        assert response.status_code == 302
        assert rows(spec) == [{"title": "One"}, {"title": "Two"}]

    def test_a_principal_the_class_check_refuses_is_never_offered_the_page(self, ada: Any) -> None:
        with pytest.raises(PermissionDenied, match="Only editors"):
            get(formset_view(many_spec(permissions=[Refuse()])), ada)

    def test_a_principal_the_scope_check_refuses_is_never_offered_the_page(self, ada: Any) -> None:
        # Asked of nothing, as dispatch asks it of a many call, and as the
        # single page asks it of a create: the post would be refused too.
        check = Scoped(admits=False)
        view = formset_view(many_spec(permissions=[check]))
        with pytest.raises(PermissionDenied, match="No batch may be added here"):
            get(view, ada)
        with pytest.raises(PermissionDenied, match="No batch may be added here"):
            post(view, ada, posted({"title": "One"}))
        assert check.scopes == [None, None]

    def test_the_scope_check_is_asked_once_a_post(self, ada: Any) -> None:
        # By dispatch alone: asked before it too, it would run twice.
        check = Scoped()
        response = post(formset_view(many_spec(permissions=[check])), ada, posted({"title": "One"}))
        assert response.status_code == 302
        assert check.scopes == [None]

    def test_a_service_s_refusal_asks_the_scope_check_once(self, ada: Any) -> None:
        # Dispatch asked it before the service ran, and only an
        # ``InvalidArguments`` may have been raised before it was asked.
        def conflicts(**kwargs: Any) -> None:
            raise ServiceConflict("Already shelved.")

        check = Scoped()
        spec = ServiceSpec(
            service=conflicts, permissions=[check], many=True, validator=FormValidator(BookRow)
        )
        response = post(formset_view(spec), ada, posted({"title": "One"}))
        assert response.status_code == 409
        assert check.scopes == [None]

    @pytest.mark.parametrize(
        ("form_class", "data"),
        [
            pytest.param(AuthoredRow, posted({"author": "x"}), id="a-row-s-coercion"),
            pytest.param(BookRow, {"save": "pressed"}, id="the-management-form"),
        ],
    )
    def test_a_post_the_page_s_reading_refuses_asks_the_scope_check_first(
        self, ada: Any, form_class: type[forms.BaseForm], data: dict[str, str]
    ) -> None:
        # Refused before dispatch, which would have asked it: a principal the
        # scope check refuses is denied, never shown the formset, as the
        # single page denies one before reading its form.
        check = Scoped(admits=False)
        view = formset_view(many_spec(form_class, permissions=[check]))
        with pytest.raises(PermissionDenied, match="No batch may be added here"):
            post(view, ada, data)
        assert check.scopes == [None]

    def test_a_post_the_page_s_reading_refuses_asks_the_scope_check_once(self, ada: Any) -> None:
        check = Scoped()
        response = post(
            formset_view(many_spec(AuthoredRow, permissions=[check])), ada, posted({"author": "x"})
        )
        assert response.status_code == 400
        assert check.scopes == [None]

    @pytest.mark.parametrize(
        ("kwargs", "rows"),
        [
            pytest.param({"many_min_length": 1}, 0, id="short-of-the-list-s-minimum"),
            pytest.param({"many_max_length": 1}, 2, id="past-the-list-s-maximum"),
            pytest.param(
                {"reads": Parameters.of(Parameter("shelf", "integer", required=True))},
                1,
                id="a-required-read-no-route-supplies",
            ),
        ],
    )
    def test_a_post_the_shape_check_refuses_is_denied_to_a_principal_the_scope_check_refuses(
        self, ada: Any, kwargs: dict[str, Any], rows: int
    ) -> None:
        # Refused by dispatch's shape check, before the scope check it would
        # have asked: a crafted management form must not show a principal
        # the GET denies the formset, every author the choice field lists.
        secret = Author.objects.create(name="Secret")
        check = Scoped(admits=False)
        view = formset_view(many_spec(AuthoredRow, permissions=[check], **kwargs))
        with pytest.raises(PermissionDenied, match="No batch may be added here"):
            post(view, ada, posted(*[{"author": str(secret.pk)}] * rows))
        assert check.scopes == [None]

    def test_a_row_the_validator_refuses_asks_the_scope_check_again(self, ada: Any) -> None:
        # Dispatch asked it before the Validator refused the row, and no grant
        # vouches for a scope check, so the page asks it a second time.
        check = Scoped()
        response = post(
            formset_view(many_spec(BookRow, permissions=[check])), ada, posted({"title": "shut"})
        )
        assert response.status_code == 400
        assert check.scopes == [None, None]

    def test_a_post_refused_at_its_top_level_is_answered_before_the_scope_check(
        self, ada: Any
    ) -> None:
        # As on the single page, whose route value its reads refuse is
        # answered at 400 with the scope check unasked: there are no
        # arguments to ask it with.
        check = Scoped(admits=False)
        spec = many_spec(
            permissions=[check],
            reads=Parameters.of(Parameter("shelf", "integer", required=True)),
        )
        data = posted({"title": "One"})
        response = post(formset_view(spec), ada, data, url_kwargs={"shelf": "x"})
        assert (response.status_code, non_form(response)) == (400, ["shelf: Enter a whole number."])
        assert check.scopes == []

    def test_a_route_value_the_reads_refuse_is_a_bad_request(self, ada: Any) -> None:
        spec = many_spec(reads=Parameters.of(Parameter("shelf", "integer", required=True)))
        with pytest.raises(BadRequest, match="shelf"):
            get(formset_view(spec), ada, shelf="x")


class TestFoldingThePost:
    """Each sent form's row, in order, as one list under ``many_argument``."""

    def test_rows_fold_into_the_many_argument_list_in_order(self, ada: Any) -> None:
        spec = many_spec()
        data = posted({"title": "One"}, {"title": "Two", "count": "2"}, {"title": "Three"})
        response = post(formset_view(spec), ada, data)
        assert (response.status_code, response.url) == (302, "/done/")
        assert rows(spec) == [
            {"title": "One", "urgent": False},
            {"title": "Two", "urgent": False, "count": 2},
            {"title": "Three", "urgent": False},
        ]
        # The service is handed the list, never a row's fields spread.
        assert [row["title"] for row in spec.service.calls[0][spec.many_argument]] == [
            "One",
            "Two",
            "Three",
        ]

    def test_an_unchanged_extra_form_sends_no_row(self, ada: Any) -> None:
        # Three forms offered, the middle one left as it came: two rows.
        spec = many_spec()
        response = post(formset_view(spec), ada, posted({"title": "One"}, {}, {"title": "Three"}))
        assert response.status_code == 302
        assert [row["title"] for row in rows(spec)] == ["One", "Three"]

    def test_a_required_form_left_blank_is_sent_and_refused(self, ada: Any) -> None:
        # The first form is the list's minimum, so it is sent left blank,
        # and the kernel refuses its row.
        spec = many_spec(many_min_length=1)
        response = post(formset_view(spec), ada, posted({}, {"title": "Two"}))
        assert response.status_code == 400
        assert row_errors(response) == [{"title": [REQUIRED]}, {}]
        assert spec.service.calls == []

    def test_no_management_form_key_becomes_an_argument(self, ada: Any) -> None:
        # The closed argument set refuses a key no parameter declares, so a
        # management key sent as an argument would be a 400, not a redirect.
        spec = many_spec(reads=Parameters.of(Parameter("shelf", "integer")))
        data = posted({"title": "One"}, csrfmiddlewaretoken="token", save="Save")
        response = post(formset_view(spec), ada, data)
        assert response.status_code == 302
        call = spec.service.calls[0]
        assert not {key for key in call if key.startswith("form-")}
        assert set(rows(spec)[0]) == {"title", "urgent"}

    def test_the_route_is_read_once_beside_the_list(self, ada: Any) -> None:
        spec = many_spec(reads=Parameters.of(Parameter("shelf", "integer", required=True)))
        data = posted({"title": "One"}, {"title": "Two"})
        response = post(formset_view(spec), ada, data, url_kwargs={"shelf": "4"})
        assert response.status_code == 302
        assert spec.service.calls[0]["shelf"] == 4
        assert all("shelf" not in row for row in rows(spec))

    def test_route_kwargs_name_the_call_s_parameters_never_a_row_s(self, ada: Any) -> None:
        # Read against the top level, so a name only the call declares is
        # allowed, and no row is checked against it.
        spec = many_spec(reads=Parameters.of(Parameter("shelf", "integer", required=True)))
        view = formset_view(spec, route_kwargs=("shelf",))
        response = post(view, ada, posted({"title": "One"}), url_kwargs={"shelf": "4"})
        assert response.status_code == 302
        assert spec.service.calls[0]["shelf"] == 4

    def test_a_post_without_the_management_form_is_refused_in_django_s_words(
        self, ada: Any
    ) -> None:
        spec = many_spec()
        response = post(formset_view(spec), ada, {"form-0-title": "One"})
        assert response.status_code == 400
        [message] = non_form(response)
        assert message.startswith(MANAGEMENT)
        assert "form-TOTAL_FORMS" in message
        assert spec.service.calls == []

    def test_a_checked_box_is_true_and_an_unchecked_one_false(self, ada: Any) -> None:
        spec = many_spec()
        post(formset_view(spec), ada, posted({"title": "One", "urgent": "on"}, {"title": "Two"}))
        assert [row["urgent"] for row in rows(spec)] == [True, False]

    def test_a_blank_optional_field_is_absent(self, ada: Any) -> None:
        spec = many_spec()
        post(formset_view(spec), ada, posted({"title": "One", "count": "", "due": ""}))
        assert rows(spec) == [{"title": "One", "urgent": False}]

    def test_a_date_in_the_form_s_input_format_is_read_as_the_form_reads_it(self, ada: Any) -> None:
        spec = many_spec()
        response = post(formset_view(spec), ada, posted({"title": "One", "due": "10/25/2006"}))
        assert response.status_code == 302
        assert str(rows(spec)[0]["due"]) == "2006-10-25"

    def test_a_choice_s_key_is_coerced_in_its_row(self, ada: Any) -> None:
        # A choice posts its key as a string, which the kernel's shape check
        # refuses for an integer key unless the row is coerced first.
        author = Author.objects.create(name="Ursula")
        spec = many_spec(AuthoredRow)
        response = post(formset_view(spec), ada, posted({"author": str(author.pk)}))
        assert response.status_code == 302
        assert spec.service.calls[0]["data"] == [{"author": author}]

    def test_a_row_its_coercion_refuses_is_refused_at_its_form(self, ada: Any) -> None:
        # Coerced per row, every row's refusal collected under its index
        # before any is raised, as the kernel collects its own.
        spec = many_spec(AuthoredRow)
        author = Author.objects.create(name="Ursula")
        data = posted({"author": "x"}, {"author": str(author.pk)}, {"author": "y"})
        response = post(formset_view(spec), ada, data)
        assert response.status_code == 400
        errors = row_errors(response)
        assert [list(each) for each in errors] == [["author"], [], ["author"]]
        # Every row's refusal at its form, none of them read as the call's.
        assert non_form(response) == []
        assert spec.service.calls == []


class TestSplittingTheRefusal:
    """A refusal of row *i* on the form that sent it, and the list's own on the formset."""

    def test_a_refusal_of_row_i_lands_on_the_form_that_sent_it(self, ada: Any) -> None:
        # The middle form is dropped, so the third form sends list row 1.
        spec = many_spec()
        data = posted({"title": "One"}, {}, {"title": "toolong"})
        response = post(formset_view(spec), ada, data)
        assert response.status_code == 400
        assert row_errors(response) == [{}, {}, {"title": [TOO_LONG]}]
        assert non_form(response) == []

    @pytest.mark.parametrize("index", [1, -1], ids=["past-the-rows-sent", "negative"])
    def test_a_refusal_of_a_row_the_post_did_not_send_lands_on_the_non_form_errors(
        self, ada: Any, index: int
    ) -> None:
        # A service's tree reaches the page as it raised it, so it can name a
        # row no form sent: never an IndexError, and never the refusal of
        # another form counted from the end of the list.
        def refuses_a_row(**arguments: Any) -> None:
            raise InvalidArguments({"items": {index: {"title": ["No such row."]}}})

        spec = ServiceSpec(
            service=refuses_a_row, many=True, validator=Watched(BookRow), permissions=OPEN
        )
        response = post(formset_view(spec), ada, posted({"title": "One"}))
        assert response.status_code == 400
        assert row_errors(response) == [{}]
        assert non_form(response) == [f"items.{index}.title: No such row."]

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"many_max_length": 1}, "Ensure this field has no more than 1 elements."),
            ({"validator": NoTwoAlike(BookRow)}, "Two rows share a title."),
        ],
        ids=["the-list-s-length", "the-validator-s-word-on-the-list"],
    )
    def test_a_refusal_of_the_list_lands_on_the_non_form_errors(
        self, ada: Any, kwargs: dict[str, Any], message: str
    ) -> None:
        spec = many_spec(**kwargs)
        response = post(formset_view(spec), ada, posted({"title": "One"}, {"title": "One"}))
        assert response.status_code == 400
        [placed] = non_form(response)
        assert placed.startswith(message)
        assert row_errors(response) == [{}, {}]

    def test_a_route_value_is_a_prefixed_non_form_error(self, ada: Any) -> None:
        # Beside a row's refusal, in the same post.
        spec = many_spec(reads=Parameters.of(Parameter("shelf", "integer", required=True)))
        data = posted({"title": "toolong"})
        response = post(formset_view(spec), ada, data, url_kwargs={"shelf": "x"})
        assert response.status_code == 400
        assert non_form(response) == ["shelf: Enter a whole number."]
        assert row_errors(response) == [{"title": [TOO_LONG]}]

    def test_a_service_s_tree_under_another_key_is_a_prefixed_non_form_error(
        self, ada: Any
    ) -> None:
        # A mapping under a key that is not the list's is about the call,
        # read with its path rather than as rows.
        def refuses(**pool: Any) -> None:
            raise ServiceValidationError({"shelf": {"code": ["Unknown shelf."]}})

        spec = ServiceSpec(
            service=refuses, permissions=OPEN, many=True, validator=FormValidator(BookRow)
        )
        response = post(formset_view(spec), ada, posted({"title": "One"}))
        assert response.status_code == 400
        assert non_form(response) == ["shelf.code: Unknown shelf."]
        assert row_errors(response) == [{}]

    def test_a_service_s_tree_under_the_list_lands_on_the_forms(self, ada: Any) -> None:
        # A service may address rows as the kernel does, by index.
        def refuses(**pool: Any) -> None:
            raise ServiceValidationError({"items": {1: {"title": ["Already on the shelf."]}}})

        spec = ServiceSpec(
            service=refuses, permissions=OPEN, many=True, validator=FormValidator(BookRow)
        )
        response = post(formset_view(spec), ada, posted({"title": "One"}, {"title": "Two"}))
        assert row_errors(response) == [{}, {"title": ["Already on the shelf."]}]
        assert non_form(response) == []

    def test_a_row_s_own_check_answers_beside_another_row_s_shape_refusal(self, ada: Any) -> None:
        # The shape check refuses the blank title, so the Validator never
        # runs on the row; the page's form says the rest in the same post.
        spec = many_spec()
        response = post(formset_view(spec), ada, posted({"count": "13"}, {"title": "Two"}))
        assert response.status_code == 400
        assert row_errors(response) == [{"title": [REQUIRED], "__all__": [UNLUCKY]}, {}]

    def test_a_partial_spec_s_rows_carry_the_refusal_alone(self, ada: Any) -> None:
        # Its Validator leaves the blank title out, and the page's form
        # validated whole would call it required.
        spec = many_spec(partial=True)
        response = post(formset_view(spec), ada, posted({"count": "13"}))
        assert response.status_code == 400
        assert row_errors(response) == [{"__all__": [UNLUCKY]}]

    def test_a_route_value_and_a_row_s_coercion_are_refused_in_one_post(self, ada: Any) -> None:
        # On a partial spec, whose forms carry the refusal alone, so the
        # row's refusal is seen only if the one raised still carries it: the
        # scope check asked beside a refused reading reads the route again
        # and must not raise its own refusal in its place.
        spec = many_spec(
            AuthoredRow,
            partial=True,
            reads=Parameters.of(Parameter("shelf", "integer", required=True)),
        )
        response = post(formset_view(spec), ada, posted({"author": "x"}), url_kwargs={"shelf": "x"})
        assert response.status_code == 400
        assert non_form(response) == ["shelf: Enter a whole number."]
        assert [list(each) for each in row_errors(response)] == [["author"]]

    def test_a_conflict_carries_its_message_alone(self, ada: Any) -> None:
        def taken(**pool: Any) -> None:
            raise ServiceConflict("Those titles are taken.")

        Cleaned.cleans = 0
        spec = ServiceSpec(
            service=taken, permissions=OPEN, many=True, validator=FormValidator(Cleaned)
        )
        response = post(formset_view(spec), ada, posted({"title": "One"}, {"title": "Two"}))
        assert response.status_code == 409
        assert non_form(response) == ["Those titles are taken."]
        assert row_errors(response) == [{}, {}]
        # The Validator's two cleans, and none of the page's.
        assert Cleaned.cleans == 2

    def test_a_question_is_answered_by_its_message_alone(self, ada: Any) -> None:
        def asks(**pool: Any) -> None:
            raise AdditionalInputRequired("Add all three?")

        spec = ServiceSpec(
            service=asks, permissions=OPEN, many=True, validator=FormValidator(BookRow)
        )
        response = post(formset_view(spec), ada, posted({"title": "One"}))
        assert response.status_code == 422
        assert non_form(response) == ["Add all three?"]
        assert response.context_data["requested_input"] == {
            "message": "Add all three?",
            "schema": None,
        }

    def test_a_question_naming_a_row_s_field_is_raised_never_rendered(self, ada: Any) -> None:
        # The call's top level is the list and the reads: no row field.
        def asks(**pool: Any) -> None:
            raise AdditionalInputRequired("Urgent?", schema={"urgent": {"type": "boolean"}})

        spec = ServiceSpec(
            service=asks, permissions=OPEN, many=True, validator=FormValidator(BookRow)
        )
        with pytest.raises(ImproperlyConfigured, match="urgent"):
            post(formset_view(spec), ada, posted({"title": "One"}))

    def test_a_denial_is_raised_for_the_host_s_403(self, ada: Any) -> None:
        def refuses(**pool: Any) -> None:
            raise NotPermitted("Not your shelf.")

        spec = ServiceSpec(
            service=refuses, permissions=OPEN, many=True, validator=FormValidator(BookRow)
        )
        with pytest.raises(PermissionDenied, match="Not your shelf"):
            post(formset_view(spec), ada, posted({"title": "One"}))


class TestAPostThatDoesNotCommit:
    """``commit_button`` and ``add_button``: the page again, and nothing dispatched."""

    def test_an_add_button_re_renders_with_one_more_form_and_no_dispatch(self, ada: Any) -> None:
        spec = many_spec()
        # A GET carries no post to add to.
        assert len(get(adding(spec), ada).context_data["formset"].forms) == 1
        response = post(adding(spec), ada, posted({"title": "One"}, add="Add a row"))
        assert response.status_code == 200
        formset = response.context_data["formset"]
        assert len(formset.forms) == 2
        assert formset.forms[0]["title"].value() == "One"
        assert response.context_data["refreshed"] is True
        assert spec.service.calls == []

    def test_the_add_button_stops_at_the_list_s_maximum(self, ada: Any) -> None:
        data = posted({"title": "One"}, {"title": "Two"}, add="Add a row")
        response = post(adding(many_spec(many_max_length=2)), ada, data)
        assert len(response.context_data["formset"].forms) == 2

    def test_an_add_button_beside_a_broken_management_form_adds_nothing(self, ada: Any) -> None:
        response = post(adding(many_spec()), ada, {"form-0-title": "One", "add": "Add a row"})
        assert response.status_code == 200
        assert len(response.context_data["formset"].forms) == 0

    def test_a_refresh_validates_nothing(self, ada: Any) -> None:
        # Neither a row past its length nor a broken management form is
        # called out on a post that did not ask for a verdict.
        response = post(adding(many_spec()), ada, posted({"title": "toolong"}))
        assert response.status_code == 200
        assert row_errors(response) == [{}]
        assert non_form(response) == []
        response = post(adding(many_spec()), ada, {"refresh": "1"})
        assert non_form(response) == []

    def test_the_commit_button_commits(self, ada: Any) -> None:
        spec = many_spec()
        response = post(adding(spec), ada, posted({"title": "One"}, save="Save"))
        assert (response.status_code, response.url) == (302, "/done/")
        assert [row["title"] for row in rows(spec)] == ["One"]


MESSAGES_STACK = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]


@pytest.fixture
def stack(settings: Any) -> Callable[..., Any]:
    """Post through Django's handler and the messages middleware, as a project serves the page."""
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "django.contrib.messages"]
    settings.MIDDLEWARE = MESSAGES_STACK
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

    def through(view: Any, user: Any, data: Mapping[str, Any]) -> Any:
        urls = types.ModuleType("one_page_urls")
        urls.urlpatterns = [path("page/", view), *urlpatterns]  # ty: ignore[unresolved-attribute]
        settings.ROOT_URLCONF = urls
        client = Client()
        client.force_login(user)
        return client.post("/page/", data)

    return through


def sent(response: Any) -> list[str]:
    return [each.message for each in get_messages(response.wsgi_request)]


class TestASuccess:
    def test_the_single_page_s_formatting_would_raise_once_the_write_ran(self, ada: Any) -> None:
        # Why this page formats with a count: the base's, a name over the
        # cleaned data, cannot read a many call's list, and fails only after
        # the service has run.
        class FormattedAsTheSinglePage(SpecFormSetView):
            get_success_message = SpecPageBase.get_success_message

        spec = many_spec()
        view = FormattedAsTheSinglePage.as_view(
            spec=spec,
            template_name=TEMPLATE,
            success_url="/done/",
            success_message="%(title)s was added.",
        )
        with pytest.raises(TypeError):
            post(view, ada, posted({"title": "One"}))
        assert len(spec.service.calls) == 1

    def test_the_default_message_is_formatted_with_the_rows_count(
        self, ada: Any, stack: Callable[..., Any]
    ) -> None:
        view = formset_view(many_spec(), success_message="%(count)d books were added.")
        response = stack(view, ada, posted({"title": "One"}, {}, {"title": "Two"}))
        assert response.status_code == 302
        assert sent(response) == ["2 books were added."]

    def test_an_override_reads_the_rows(self, ada: Any, stack: Callable[..., Any]) -> None:
        class Named(SpecFormSetView):
            def get_success_message(self, result: DispatchResult) -> str:
                return ", ".join(row["title"] for row in result.data)

        view = Named.as_view(spec=many_spec(), template_name=TEMPLATE, success_url="/done/")
        response = stack(view, ada, posted({"title": "One"}, {"title": "Two"}))
        assert sent(response) == ["One, Two"]


class TestTheFragment:
    """An htmx request gets the form's fragment, the outcome decided as for the page."""

    def view(self, spec: ServiceSpec) -> Any:
        return formset_view(spec, fragment_template_name=FRAGMENT)

    def test_a_get_is_the_fragment(self, ada: Any) -> None:
        response = self.view(many_spec()).__call__(signed_in(FACTORY.get("/", **HTMX), ada))
        assert response.template_name == [FRAGMENT]
        assert b"<h1>" not in response.render().content

    def test_a_refusal_is_the_fragment_at_200(self, ada: Any) -> None:
        response = post(self.view(many_spec()), ada, posted({"title": "toolong"}), **HTMX)
        assert (response.status_code, response.template_name) == (200, [FRAGMENT])
        assert row_errors(response) == [{"title": [TOO_LONG]}]

    def test_a_success_is_hx_redirect(self, ada: Any) -> None:
        response = post(self.view(many_spec()), ada, posted({"title": "One"}), **HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/done/")
