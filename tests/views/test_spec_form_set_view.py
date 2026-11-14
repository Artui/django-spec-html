from __future__ import annotations

import dataclasses
import re
import types
from collections.abc import Callable, Iterator, Mapping
from typing import Annotated, Any, TypedDict

import pytest
from django import forms
from django.contrib.messages import get_messages
from django.core.exceptions import (
    BadRequest,
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.http import HttpResponse
from django.test import Client
from django.test.signals import template_rendered
from django.urls import path
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    NON_FIELD_ERRORS,
    AdditionalInputRequired,
    DataclassPresenter,
    DataclassValidator,
    DispatchResult,
    FieldMarking,
    FilePresenter,
    FormValidator,
    InvalidArguments,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceConflict,
    ServiceSpec,
    ServiceValidationError,
    Validated,
    ValidationContext,
    Validator,
)

from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from django_spec_html.views.utils import SpecPageBase
from tests.adapter_app.models import Author
from tests.dispatch.utils import OPEN, Record, Refuse, Titled, make_user, notes_of
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, DeclaredByTheRequest, Handed, handed_spec, signed_in
from tests.views.utils import Fresh, SeesTenantAtEachQuestion

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
    """``FormValidator``, recording each row list it is handed: what the page sent, checked."""

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
    """A batch write validated row by row by ``form_class``, whose service records its input."""
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("validator", Watched(form_class))
    return ServiceSpec(service=Record(), many=True, **kwargs)


def formset_view(spec: ServiceSpec, **initkwargs: Any) -> Any:
    initkwargs.setdefault("template_name", TEMPLATE)
    initkwargs.setdefault("success_url", "/done/")
    return SpecFormSetView.as_view(spec=spec, **initkwargs)


def adding(spec: ServiceSpec, **attributes: Any) -> Any:
    """A page committing only from ``save``, adding from ``add``, set as a subclass sets them."""
    attributes = {
        "template_name": TEMPLATE,
        "success_url": "/done/",
        "commit_button": "save",
        "add_button": "add",
        **attributes,
    }
    return type("Adding", (SpecFormSetView,), attributes).as_view(spec=spec)


def posted(*rows: Mapping[str, Any], total: int | None = None, **extra: str) -> dict[str, Any]:
    """What a browser posts for ``rows``: the management form, then each row's fields by prefix."""
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
    """The rows the page sent, once, as the kernel handed them to the Validator; the call ran."""
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
        with pytest.raises(ImproperlyConfigured, match="leave display_target off"):
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
        # Asked of nothing, as dispatch asks a many call, and as the single page asks a create.
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
        # Dispatch asked it before the service ran; only InvalidArguments may precede that.
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
        # Refused before dispatch asks it: a refused principal never sees the formset.
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
        # A crafted management form must not show a principal the GET denies the formset.
        secret = Author.objects.create(name="Secret")
        check = Scoped(admits=False)
        view = formset_view(many_spec(AuthoredRow, permissions=[check], **kwargs))
        with pytest.raises(PermissionDenied, match="No batch may be added here"):
            post(view, ada, posted(*[{"author": str(secret.pk)}] * rows))
        assert check.scopes == [None]

    def test_a_row_the_validator_refuses_asks_the_scope_check_again(self, ada: Any) -> None:
        # Dispatch asked before the Validator refused the row, and no grant vouches; ask again.
        check = Scoped()
        response = post(
            formset_view(many_spec(BookRow, permissions=[check])), ada, posted({"title": "shut"})
        )
        assert response.status_code == 400
        assert check.scopes == [None, None]

    def test_a_post_refused_at_its_top_level_is_answered_before_the_scope_check(
        self, ada: Any
    ) -> None:
        # With route_refusal_404 off, a refused route value is a 400, the scope check unasked.
        check = Scoped(admits=False)
        spec = many_spec(
            permissions=[check],
            reads=Parameters.of(Parameter("shelf", "integer", required=True)),
        )
        data = posted({"title": "One"})
        view = formset_view(spec, route_refusal_404=False)
        response = post(view, ada, data, url_kwargs={"shelf": "x"})
        assert (response.status_code, non_form(response)) == (400, ["shelf: Enter a whole number."])
        assert check.scopes == []

    def test_a_route_value_the_reads_refuse_is_a_bad_request(self, ada: Any) -> None:
        spec = many_spec(reads=Parameters.of(Parameter("shelf", "integer", required=True)))
        with pytest.raises(BadRequest, match="shelf"):
            get(formset_view(spec, route_refusal_404=False), ada, shelf="x")


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
        # The first form is the list's minimum, sent blank, so the kernel refuses its row.
        spec = many_spec(many_min_length=1)
        response = post(formset_view(spec), ada, posted({}, {"title": "Two"}))
        assert response.status_code == 400
        assert row_errors(response) == [{"title": [REQUIRED]}, {}]
        assert spec.service.calls == []

    def test_no_management_form_key_becomes_an_argument(self, ada: Any) -> None:
        # The closed argument set refuses an undeclared key, so a management key would be a 400.
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
        # Read against the top level, so a name only the call declares is allowed.
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
        # A choice posts its key as a string, which the shape check refuses unless coerced first.
        author = Author.objects.create(name="Ursula")
        spec = many_spec(AuthoredRow)
        response = post(formset_view(spec), ada, posted({"author": str(author.pk)}))
        assert response.status_code == 302
        assert spec.service.calls[0]["data"] == [{"author": author}]

    def test_a_row_its_coercion_refuses_is_refused_at_its_form(self, ada: Any) -> None:
        # Coerced per row; every refusal is collected under its index before any is raised.
        spec = many_spec(AuthoredRow)
        author = Author.objects.create(name="Ursula")
        data = posted({"author": "x"}, {"author": str(author.pk)}, {"author": "y"})
        response = post(formset_view(spec), ada, data)
        assert response.status_code == 400
        errors = row_errors(response)
        assert [list(each) for each in errors] == [["author"], [], ["author"]]
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
        # A service's tree can name a row no form sent, so the page must not index the list.
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
        view = formset_view(spec, route_refusal_404=False)
        response = post(view, ada, data, url_kwargs={"shelf": "x"})
        assert response.status_code == 400
        assert non_form(response) == ["shelf: Enter a whole number."]
        assert row_errors(response) == [{"title": [TOO_LONG]}]

    def test_a_service_s_tree_under_another_key_is_a_prefixed_non_form_error(
        self, ada: Any
    ) -> None:
        # A mapping under a key that is not the list's is about the call, not rows.
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
        # The shape check refuses the blank title, so the Validator never runs on the row.
        spec = many_spec()
        response = post(formset_view(spec), ada, posted({"count": "13"}, {"title": "Two"}))
        assert response.status_code == 400
        assert row_errors(response) == [{"title": [REQUIRED], "__all__": [UNLUCKY]}, {}]

    def test_a_partial_spec_s_rows_carry_the_refusal_alone(self, ada: Any) -> None:
        # The Validator leaves the blank title out, so the form calls it required.
        spec = many_spec(partial=True)
        response = post(formset_view(spec), ada, posted({"count": "13"}))
        assert response.status_code == 400
        assert row_errors(response) == [{"__all__": [UNLUCKY]}]

    def test_a_route_value_and_a_row_s_coercion_are_refused_in_one_post(self, ada: Any) -> None:
        # On a partial spec the refusal survives only if the one raised still carries it.
        spec = many_spec(
            AuthoredRow,
            partial=True,
            reads=Parameters.of(Parameter("shelf", "integer", required=True)),
        )
        view = formset_view(spec, route_refusal_404=False)
        response = post(view, ada, posted({"author": "x"}), url_kwargs={"shelf": "x"})
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
        # Neither an over-long list nor a broken management form is called out without a verdict.
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


class TestOneResolutionPerRequest:
    def test_one_resolution_from_the_class_level_check_to_the_run(self, ada: Any) -> None:
        # No target, so dispatch's scope check runs on the admission's seeds, resolved once.
        fresh = Fresh()
        check = SeesTenantAtEachQuestion()
        spec = many_spec(permissions=[check])
        view = formset_view(spec, pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=fresh))
        assert post(view, ada, posted({"title": "One"})).status_code == 302
        assert len(fresh.resolved) == 1
        seen = fresh.resolved[0]
        assert [question for question, _ in check.asked] == ["class", "scope"]
        assert all(tenant is seen for _, tenant in check.asked)
        assert spec.service.calls[0]["tenant"] is seen

    def test_a_refused_post_asks_the_scope_check_again_on_the_same_seeds(self, ada: Any) -> None:
        # A post the page's reading refuses leaves the scope check unasked; the page asks it.
        fresh = Fresh()
        check = SeesTenantAtEachQuestion()
        view = formset_view(
            many_spec(AuthoredRow, permissions=[check]),
            pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=fresh),
        )
        assert post(view, ada, posted({"author": "x"})).status_code == 400
        assert len(fresh.resolved) == 1
        seen = fresh.resolved[0]
        assert [question for question, _ in check.asked] == ["class", "scope"]
        assert all(tenant is seen for _, tenant in check.asked)


def returning(spec: ServiceSpec, **attributes: Any) -> Any:
    """A page that reads ``next``, set as a host's subclass sets it."""
    attributes = {
        "template_name": TEMPLATE,
        "success_url": "/done/",
        "redirect_field_name": "next",
        **attributes,
    }
    return type("Returning", (SpecFormSetView,), attributes).as_view(spec=spec)


class TestReturningToNext:
    def test_a_success_redirects_to_next(self, ada: Any) -> None:
        spec = many_spec()
        request = FACTORY.post("/?next=/list/%3Fpage%3D3", data=posted({"title": "One"}))
        response = returning(spec)(signed_in(request, ada))
        assert (response.status_code, response.url) == (302, "/list/?page=3")
        # Not a key under the formset's prefix, so no row and no argument reads it.
        assert [row["title"] for row in rows(spec)] == ["One"]
        assert "next" not in spec.service.calls[0]

    def test_a_refused_post_carries_it(self, ada: Any) -> None:
        data = posted({"title": "toolong"}, next="/list/")
        response = post(returning(many_spec()), ada, data)
        assert response.status_code == 400
        assert response.context_data["next"] == "/list/"

    @pytest.mark.parametrize(
        ("attributes", "message"),
        [
            (
                {"redirect_field_name": "form-0-title"},
                "redirect_field_name names 'form-0-title', under the formset's 'form-' prefix",
            ),
            (
                {"commit_button": "save", "add_button": "next"},
                "redirect_field_name and add_button both name 'next'",
            ),
        ],
        ids=["under-the-prefix", "the-add-button"],
    )
    def test_a_name_a_post_carries_otherwise_is_refused(
        self, attributes: dict[str, str], message: str
    ) -> None:
        with pytest.raises(ImproperlyConfigured, match=message):
            returning(many_spec(), **attributes)

    def test_a_name_beside_both_buttons_builds(self) -> None:
        returning(many_spec(), commit_button="save", add_button="add")


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


def test_every_stage_is_handed_the_request_and_the_page(ada: Any) -> None:
    # A batch has no target: the checks, affordance, form keywords and service name both.
    handed = Handed()
    view = formset_view(handed_spec(handed, many=True, instance_selector_spec=None))
    request = signed_in(FACTORY.get("/"), ada)
    assert view(request).status_code == 200
    assert handed.stages() == {"class", "affordance", "form"}
    page = handed.one_view(request)
    assert isinstance(page, SpecFormSetView)
    assert page.request is request
    assert handed.one_transport() == "html"
    handed.reset()
    request = signed_in(FACTORY.post("/", data=posted({"title": "One"})), ada)
    assert view(request).status_code == 302
    assert handed.stages() == {"class", "affordance", "form", "service"}
    page = handed.one_view(request)
    assert isinstance(page, SpecFormSetView)
    assert page.request is request
    assert handed.one_transport() == "html"


class TestTheCallsDeclaration:
    """The page reads a spec's parameters with the request's call context, as dispatch does."""

    def test_each_row_is_read_as_the_call_declares_it(self, ada: Any) -> None:
        # The call declares the count a string; read without the context, "3" is coerced to 3.
        validator = DeclaredByTheRequest()
        spec = ServiceSpec(service=Record(), many=True, permissions=OPEN, validator=validator)
        data = posted({"title": "Hi", "count": "3"})
        request = signed_in(FACTORY.post("/", data=data), ada)
        response = formset_view(spec)(request)
        assert response.status_code == 302
        assert spec.service.calls[0]["data"][0]["count"] == "3"
        assert validator.one_context()["request"] is request


class TitleForm(forms.Form):
    title = forms.CharField()


def as_text(title: str) -> bytes:
    return title.encode()


TEXT = FilePresenter("text/plain", filename="minutes.txt", render=as_text)


def file_spec(presenter: Any = TEXT, returns: Any = "Minutes", **fields: Any) -> ServiceSpec:
    fields.setdefault("validator", FormValidator(TitleForm))
    return ServiceSpec(service=Record(returns), permissions=OPEN, presenter=presenter, **fields)


def test_a_many_file_spec_is_refused_at_as_view() -> None:
    with pytest.raises(ImproperlyConfigured, match="is declared many"):
        SpecFormSetView.as_view(spec=file_spec(many=True), success_url="/done/")


ROWS = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "spec-form-set-rows"}


ROW_TEMPLATE = "django_spec_html/spec_form_set_row.html"


class TitleRow(forms.Form):
    title = forms.CharField(max_length=20)


class AddRowOwnedRow(forms.Form):
    """A row whose ``__init__`` takes a keyword, which it shows as the title's initial value."""

    title = forms.CharField()

    def __init__(self, *args: Any, owner: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["title"].initial = f"by {owner.username}"


def add_row_many_spec(validator: FormValidator | None = None, **kwargs: Any) -> ServiceSpec:
    validator = validator or FormValidator(TitleRow)
    return ServiceSpec(service=Record(), permissions=OPEN, many=True, validator=validator, **kwargs)


def add_row_adding(spec: ServiceSpec) -> Any:
    return SpecFormSetView.as_view(
        spec=spec, success_url="/done/", commit_button="save", add_button="add"
    )


def add_row_posted(*titles: str, **extra: str) -> dict[str, str]:
    data = {"form-TOTAL_FORMS": str(len(titles)), "form-INITIAL_FORMS": "0", **extra}
    data.update({f"form-{index}-title": title for index, title in enumerate(titles)})
    return data


def add_row_post(view: Any, user: Any, data: Mapping[str, str], headers: Mapping[str, str]) -> Any:
    return view(signed_in(FACTORY.post("/", data=data, **headers), user))


def oob_total(content: str) -> str:
    """The one out-of-band input the answer carries, its markup whole."""
    [found] = re.findall(r"<input[^>]*hx-swap-oob[^>]*>", content)
    return found


@pytest.fixture
def rendered_templates() -> Iterator[list[str]]:
    names: list[str] = []

    def record(sender: Any, template: Any, **kwargs: Any) -> None:
        if (template.name or "").startswith("django_spec_html/"):
            names.append(template.name)

    template_rendered.connect(record)
    yield names
    template_rendered.disconnect(record)


class TestAnHtmxAdd:
    def test_answers_only_the_new_row_and_an_out_of_band_total(self, ada: Any) -> None:
        spec = add_row_many_spec()
        response = add_row_post(
            add_row_adding(spec), ada, add_row_posted("One", add="Add another"), ROWS
        )
        content = response.content.decode()
        assert response.status_code == 200
        assert content.lstrip().startswith("<fieldset>")
        assert "<legend>Row 2</legend>" in content
        assert 'name="form-1-title"' in content
        assert "form-0-title" not in content
        assert "<form" not in content
        total = oob_total(content)
        assert 'name="form-TOTAL_FORMS"' in total
        assert 'value="2"' in total
        assert 'id="id_form-TOTAL_FORMS"' in total
        assert 'hx-swap-oob="true"' in total
        assert spec.service.calls == []

    def test_the_new_row_is_built_with_the_form_kwargs(self, ada: Any) -> None:
        validator = FormValidator(AddRowOwnedRow, form_kwargs=lambda *, user: {"owner": user})
        response = add_row_post(
            add_row_adding(add_row_many_spec(validator)), ada, add_row_posted("One", add="+"), ROWS
        )
        assert 'name="form-1-title" value="by ada"' in response.content.decode()

    def test_the_row_renders_through_the_row_template(
        self, ada: Any, rendered_templates: list[str]
    ) -> None:
        add_row_post(add_row_adding(add_row_many_spec()), ada, add_row_posted("One", add="+"), ROWS)
        assert rendered_templates == [ROW_TEMPLATE]

    def test_at_the_list_s_maximum_it_is_204_with_no_body(self, ada: Any) -> None:
        spec = add_row_many_spec(many_max_length=2)
        response = add_row_post(
            add_row_adding(spec), ada, add_row_posted("One", "Two", add="+"), ROWS
        )
        assert (response.status_code, response.content) == (204, b"")

    def test_a_broken_management_form_is_the_formset_again(self, ada: Any) -> None:
        response = add_row_post(
            add_row_adding(add_row_many_spec()), ada, {"form-0-title": "One", "add": "+"}, ROWS
        )
        assert (response.status_code, len(response.context_data["formset"].forms)) == (200, 0)


class TestEveryOtherPostIsTheFormsetAgain:
    """An add the rows container did not ask for re-renders the formset with one more form."""

    def test_an_htmx_add_targeting_elsewhere(self, ada: Any) -> None:
        headers = {"HTTP_HX_REQUEST": "true"}
        response = add_row_post(
            add_row_adding(add_row_many_spec()), ada, add_row_posted("One", add="+"), headers
        )
        assert len(response.context_data["formset"].forms) == 2

    def test_a_plain_add_carrying_the_target_header(self, ada: Any) -> None:
        headers = {"HTTP_HX_TARGET": "spec-form-set-rows"}
        response = add_row_post(
            add_row_adding(add_row_many_spec()), ada, add_row_posted("One", add="+"), headers
        )
        assert len(response.context_data["formset"].forms) == 2

    def test_an_htmx_post_that_neither_adds_nor_commits(self, ada: Any) -> None:
        response = add_row_post(
            add_row_adding(add_row_many_spec()), ada, add_row_posted("One"), ROWS
        )
        assert len(response.context_data["formset"].forms) == 1


class TestTheShippedFragment:
    def test_renders_its_rows_through_the_row_template_inside_the_container(
        self, ada: Any, rendered_templates: list[str]
    ) -> None:
        view = SpecFormSetView.as_view(spec=add_row_many_spec(), success_url="/done/", extra=2)
        content = view(signed_in(FACTORY.get("/"), ada)).render().content.decode()
        rows = content[content.index('<div id="spec-form-set-rows">') :]
        rows = rows[: rows.index("</div>\n<button")]
        assert rows.count("<fieldset>") == 2
        assert "<legend>Row 2</legend>" in rows
        assert rendered_templates.count(ROW_TEMPLATE) == 2

    def test_the_add_button_posts_its_row_into_the_container(self, ada: Any) -> None:
        content = (
            add_row_adding(add_row_many_spec())(signed_in(FACTORY.get("/"), ada))
            .render()
            .content.decode()
        )
        add = (
            '<button type="submit" name="add" formnovalidate hx-post="" '
            'hx-target="#spec-form-set-rows" hx-swap="beforeend">Add another</button>'
        )
        assert add in content


NoteSet = forms.modelformset_factory(Note, fields=["title"], can_delete=True, extra=1)


OWN_NOTES = SelectorSpec(kind=SelectorKind.LIST, selector=notes_of)


def notes_from(*, user: Any, first: int) -> Any:
    return notes_of(user=user).filter(pk__gte=first)


FROM_FIRST = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=notes_from,
    reads=Parameters.of(Parameter("first", "integer", required=True)),
)


class EditSetNoTwoAlike(forms.BaseFormSet):
    def clean(self) -> None:
        titles = [form.cleaned_data.get("title") for form in self.forms]
        if len(set(titles)) != len(titles):
            raise ValidationError("Two rows carry one title.", code="repeated")


TitleSet = forms.formset_factory(TitleRow, formset=EditSetNoTwoAlike, extra=2)


def edit_spec(**kwargs: Any) -> ServiceSpec:
    kwargs.setdefault("validator", FormValidator(NoteSet.form, formset_class=NoteSet))
    kwargs.setdefault("collection_selector_spec", OWN_NOTES)
    return ServiceSpec(service=Record(), permissions=OPEN, many=True, **kwargs)


def page(spec: ServiceSpec, **initkwargs: Any) -> Any:
    return SpecFormSetView.as_view(spec=spec, success_url="/done/", **initkwargs)


def edit_set_get(view: Any, user: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user))


def edit_set_post(view: Any, user: Any, data: Mapping[str, Any]) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user))


def management(total: int, initial: int) -> dict[str, str]:
    return {"form-TOTAL_FORMS": str(total), "form-INITIAL_FORMS": str(initial)}


@pytest.fixture
def notes(ada: Any) -> list[Note]:
    make_user("bob").notes.create(title="Not ada's")
    return [ada.notes.create(title=title) for title in ("First", "Second", "Third")]


class TestEditSetAsView:
    def test_displaying_the_set_is_still_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="display_target"):
            page(edit_spec(), display_target=True)

    def test_a_model_formset_with_no_collection_is_refused(self) -> None:
        spec = edit_spec(collection_selector_spec=None)
        with pytest.raises(ImproperlyConfigured, match="collection_selector_spec"):
            page(spec)

    def test_a_plain_formset_class_needs_no_collection(self) -> None:
        page(edit_spec(validator=FormValidator(TitleRow, formset_class=TitleSet)))


class TestEditSetGet:
    def test_one_prefilled_form_per_row_then_the_extra(self, ada: Any, notes: list[Note]) -> None:
        response = edit_set_get(page(edit_spec()), ada)
        formset = response.context_data["formset"]
        assert isinstance(formset, NoteSet)
        assert [form.instance for form in formset.forms[:3]] == notes
        assert [form["title"].value() for form in formset.forms] == [
            "First",
            "Second",
            "Third",
            None,
        ]
        assert formset.management_form["INITIAL_FORMS"].value() == 3
        content = response.render().content.decode()
        assert f'name="form-0-id" value="{notes[0].pk}"' in content
        assert 'name="form-0-DELETE"' in content

    def test_the_page_shows_the_set_s_count_not_its_rows(self, ada: Any, notes: list[Note]) -> None:
        context = edit_set_get(page(edit_spec()), ada).context_data
        assert (context["target_count"], "target_display" in context) == (3, False)

    def test_a_plain_formset_class_is_the_page_s(self, ada: Any) -> None:
        spec = edit_spec(validator=FormValidator(TitleRow, formset_class=TitleSet))
        formset = edit_set_get(page(spec), ada).context_data["formset"]
        assert (type(formset), len(formset.forms)) == (TitleSet, 2)


class TestPost:
    def test_an_update_a_delete_and_a_create_reach_the_service(
        self, ada: Any, notes: list[Note]
    ) -> None:
        first, second, third = notes
        spec = edit_spec()
        data = {
            **management(4, 3),
            "form-0-id": str(first.pk),
            "form-0-title": "First, renamed",
            "form-1-id": str(second.pk),
            "form-1-title": "Second",
            "form-1-DELETE": "on",
            "form-2-id": str(third.pk),
            "form-2-title": "Third",
            "form-3-title": "Fourth",
        }
        response = edit_set_post(page(spec), ada, data)
        assert response.status_code == 302
        [call] = spec.service.calls
        sent = call[spec.many_argument]
        assert [(row["id"], row["title"], row.get("DELETE")) for row in sent] == [
            (first, "First, renamed", False),
            (second, "Second", True),
            (third, "Third", False),
            (None, "Fourth", False),
        ]
        assert list(call["collection"]) == notes

    def test_an_unchanged_extra_form_sends_no_row(self, ada: Any, notes: list[Note]) -> None:
        spec = edit_spec()
        data = {**management(4, 3), **_unchanged(notes)}
        edit_set_post(page(spec), ada, data)
        [call] = spec.service.calls
        assert [row["id"] for row in call[spec.many_argument]] == notes

    def test_an_extra_form_only_marked_deleted_sends_no_row(
        self, ada: Any, notes: list[Note]
    ) -> None:
        spec = edit_spec()
        data = {**management(4, 3), **_unchanged(notes), "form-3-DELETE": "on"}
        assert edit_set_post(page(spec), ada, data).status_code == 302
        [call] = spec.service.calls
        assert len(call[spec.many_argument]) == 3

    def test_a_row_outside_the_set_lands_on_its_form(self, ada: Any, notes: list[Note]) -> None:
        stranger = Note.objects.get(title="Not ada's")
        spec = edit_spec()
        data = {**management(4, 3), **_unchanged(notes), "form-2-id": str(stranger.pk)}
        response = edit_set_post(page(spec), ada, data)
        assert response.status_code == 400
        assert list(response.context_data["formset"].forms[2].errors) == ["id"]
        assert spec.service.calls == []

    def test_a_set_wide_refusal_is_the_formset_s_non_form_error(self, ada: Any) -> None:
        spec = edit_spec(validator=FormValidator(TitleRow, formset_class=TitleSet))
        data = {**management(2, 0), "form-0-title": "Same", "form-1-title": "Same"}
        response = edit_set_post(page(spec), ada, data)
        formset = response.context_data["formset"]
        assert list(formset.non_form_errors()) == ["Two rows carry one title."]
        assert spec.service.calls == []

    def test_a_post_whose_set_is_unresolved_edits_no_row(self, ada: Any, notes: list[Note]) -> None:
        spec = edit_spec(collection_selector_spec=FROM_FIRST)
        request = signed_in(FACTORY.post("/", data={**management(1, 0), "form-0-title": "A"}), ada)
        response = page(spec, route_refusal_404=False)(request, first="soon")
        formset = response.context_data["formset"]
        assert (response.status_code, list(formset.get_queryset())) == (400, [])
        assert spec.service.calls == []

    def test_a_non_committing_post_keeps_the_set(self, ada: Any, notes: list[Note]) -> None:
        view = type(
            "Adding", (SpecFormSetView,), {"commit_button": "save", "add_button": "add"}
        ).as_view(spec=edit_spec(), success_url="/done/")
        data = {**management(4, 3), **_unchanged(notes), "add": "+"}
        formset = edit_set_post(view, ada, data).context_data["formset"]
        assert [form.instance for form in formset.forms[:3]] == notes
        assert len(formset.forms) == 5


def _unchanged(notes: list[Note]) -> dict[str, str]:
    data: dict[str, str] = {}
    for index, note in enumerate(notes):
        data[f"form-{index}-id"] = str(note.pk)
        data[f"form-{index}-title"] = note.title
    return data


TAKEN = "That title is taken."


REPEATED = "Two rows carry one title."


NEVER = "the form's own validator ran"


@dataclasses.dataclass
class Book:
    title: str
    pages: int | None = None


class RowFormClassBookRow(forms.Form):
    title = forms.CharField()
    pages = forms.IntegerField(required=False)


def _never(value: Any) -> None:
    raise ValidationError(NEVER)


class GuardedRow(forms.Form):
    title = forms.CharField(validators=[_never])
    pages = forms.IntegerField(required=False, validators=[_never])


class Shelving(Validator):
    """Declares ``title`` and ``pages``; refuses a taken title per row, a repeat over the set."""

    def parameters(self) -> Parameters:
        return Parameters.of(
            Parameter("title", "string", required=True), Parameter("pages", "integer")
        )

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        if arguments["title"] == "taken":
            raise InvalidArguments({"title": [TAKEN]})
        return dict(arguments)

    def validated_many(
        self,
        rows: Mapping[int, Mapping[str, Any]],
        context: ValidationContext,
        *,
        many_argument: str,
    ) -> Validated:
        titles = [row["title"] for row in rows.values()]
        if len(set(titles)) < len(titles):
            raise InvalidArguments({NON_FIELD_ERRORS: [REPEATED]})
        return super().validated_many(rows, context, many_argument=many_argument)


class RowFormClassTitled(TypedDict):
    title: str


def server_title() -> RowFormClassTitled:
    return {"title": "Emma"}


class Declared(Validator):
    """Declares ``declared`` and hands each row back; ``unsendable`` as given."""

    def __init__(self, *declared: Parameter, unsendable: frozenset[str] = frozenset()) -> None:
        self.declared = Parameters.of(*declared)
        self._unsendable = unsendable

    @property
    def unsendable(self) -> frozenset[str]:
        return self._unsendable

    def parameters(self) -> Parameters:
        return self.declared

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)


def row_form_class_many_spec(validator: Any = None, **kwargs: Any) -> ServiceSpec:
    return ServiceSpec(
        service=Record(),
        validator=Shelving() if validator is None else validator,
        permissions=OPEN,
        many=True,
        **kwargs,
    )


def row_form_class_page(
    spec: ServiceSpec, form_class: Any = RowFormClassBookRow, **initkwargs: Any
) -> Any:
    return SpecFormSetView.as_view(
        spec=spec, form_class=form_class, success_url="/done/", **initkwargs
    )


def refusal(spec: ServiceSpec, form_class: Any = RowFormClassBookRow, **initkwargs: Any) -> str:
    with pytest.raises(ImproperlyConfigured) as refused:
        row_form_class_page(spec, form_class, **initkwargs)
    return str(refused.value)


def row_form_class_management(total: int) -> dict[str, str]:
    return {"form-TOTAL_FORMS": str(total), "form-INITIAL_FORMS": "0"}


def row_form_class_post(view: Any, user: Any, data: Mapping[str, Any]) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user))


def errors(response: Any) -> list[dict[str, list[str]]]:
    return [
        {key: list(messages) for key, messages in form.errors.items()}
        for form in response.context_data["formset"].forms
    ]


class TestAFormClassOverANonFormValidator:
    def test_get_renders_a_formset_of_the_form_class(self, ada: Any) -> None:
        response = row_form_class_page(row_form_class_many_spec())(signed_in(FACTORY.get("/"), ada))
        formset = response.context_data["formset"]
        assert response.status_code == 200
        assert type(formset.forms[0]) is RowFormClassBookRow
        assert 'name="form-0-title"' in response.rendered_content

    def test_a_valid_post_sends_the_filled_rows_and_redirects(self, ada: Any) -> None:
        spec = row_form_class_many_spec()
        data = {
            **row_form_class_management(3),
            "form-0-title": "Emma",
            "form-2-title": "Persuasion",
        }
        response = row_form_class_post(row_form_class_page(spec, extra=3), ada, data)
        assert response.status_code == 302
        assert spec.service.calls[0][spec.many_argument] == [
            {"title": "Emma"},
            {"title": "Persuasion"},
        ]

    def test_a_row_refusal_lands_on_the_form_that_sent_the_row(self, ada: Any) -> None:
        data = {**row_form_class_management(3), "form-1-title": "Emma", "form-2-title": "taken"}
        response = row_form_class_post(
            row_form_class_page(row_form_class_many_spec(), extra=3), ada, data
        )
        assert response.status_code == 400
        assert errors(response) == [{}, {}, {"title": [TAKEN]}]

    def test_a_set_wide_refusal_is_the_formset_s_non_form_errors(self, ada: Any) -> None:
        data = {**row_form_class_management(2), "form-0-title": "Emma", "form-1-title": "Emma"}
        response = row_form_class_post(row_form_class_page(row_form_class_many_spec()), ada, data)
        assert list(response.context_data["formset"].non_form_errors()) == [REPEATED]
        assert errors(response) == [{}, {}]


class TestTheRowFormRendersAndIsRead:
    def test_its_validators_never_run_on_a_success(self, ada: Any) -> None:
        spec = row_form_class_many_spec()
        data = {**row_form_class_management(1), "form-0-title": "Emma", "form-0-pages": "3"}
        response = row_form_class_post(row_form_class_page(spec, GuardedRow), ada, data)
        assert response.status_code == 302
        assert spec.service.calls[0][spec.many_argument] == [{"title": "Emma", "pages": 3}]

    def test_a_refusal_carries_the_validator_s_errors_alone(self, ada: Any) -> None:
        data = {**row_form_class_management(2), "form-0-title": "Emma", "form-1-title": "taken"}
        response = row_form_class_post(
            row_form_class_page(row_form_class_many_spec(), GuardedRow), ada, data
        )
        assert errors(response) == [{}, {"title": [TAKEN]}]

    def test_a_set_wide_refusal_runs_no_row_validator(self, ada: Any) -> None:
        data = {**row_form_class_management(2), "form-0-title": "Emma", "form-1-title": "Emma"}
        response = row_form_class_post(
            row_form_class_page(row_form_class_many_spec(), GuardedRow), ada, data
        )
        assert errors(response) == [{}, {}]


class RowFormClassOwnedRow(RowFormClassBookRow):
    def __init__(self, *args: Any, shelf: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.shelf = shelf


class Owned(SpecFormSetView):
    def get_form_kwargs(self) -> Mapping[str, Any]:
        return {"shelf": "fiction"}


class TestGetFormKwargs:
    def test_the_default_is_empty(self) -> None:
        assert SpecFormSetView().get_form_kwargs() == {}

    def test_every_row_form_is_built_with_them(self, ada: Any) -> None:
        view = Owned.as_view(
            spec=row_form_class_many_spec(),
            form_class=RowFormClassOwnedRow,
            success_url="/done/",
            extra=2,
        )
        response = view(signed_in(FACTORY.get("/"), ada))
        formset = response.context_data["formset"]
        assert [form.shelf for form in formset.forms] == ["fiction", "fiction"]
        assert formset.empty_form.shelf == "fiction"

    @pytest.mark.parametrize("key", ["data", "files", "instance"])
    def test_a_keyword_the_page_sets_is_refused(self, ada: Any, key: str) -> None:
        class Overreaching(SpecFormSetView):
            def get_form_kwargs(self) -> Mapping[str, Any]:
                return {key: None}

        view = Overreaching.as_view(
            spec=row_form_class_many_spec(), form_class=RowFormClassBookRow, success_url="/done/"
        )
        with pytest.raises(ImproperlyConfigured, match=f"returned '{key}', which the page sets"):
            view(signed_in(FACTORY.get("/"), ada))

    def test_a_form_validator_page_never_asks_them(self, ada: Any) -> None:
        class Asked(SpecFormSetView):
            def get_form_kwargs(self) -> Mapping[str, Any]:
                raise AssertionError("asked")

        view = Asked.as_view(
            spec=row_form_class_many_spec(FormValidator(RowFormClassBookRow)), success_url="/done/"
        )
        assert view(signed_in(FACTORY.get("/"), ada)).status_code == 200


class SplitRow(forms.Form):
    when = forms.SplitDateTimeField()


class SubtitledRow(RowFormClassBookRow):
    subtitle = forms.CharField(required=False)


class TitleOnlyRow(forms.Form):
    title = forms.CharField()


class PagesOnlyRow(forms.Form):
    pages = forms.IntegerField(required=False)


class FloatPagesRow(forms.Form):
    title = forms.CharField()
    pages = forms.FloatField(required=False)


class OptionalTitleRow(forms.Form):
    title = forms.CharField(required=False)
    pages = forms.IntegerField(required=False)


class CleanRow(RowFormClassBookRow):
    def clean(self) -> dict[str, Any]:
        return super().clean() or {}


class CleanTitleRow(RowFormClassBookRow):
    def clean_title(self) -> str:
        return self.cleaned_data["title"]


class ShelfRow(RowFormClassBookRow):
    shelf = forms.IntegerField()


class TestAsViewRefusesPerRow:
    def test_a_validator_not_a_form_validator_without_form_class(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecFormSetView.as_view(spec=row_form_class_many_spec(), success_url="/done/")
        assert "set form_class, or the spec's validator must be a FormValidator; got Shelving" in (
            str(refused.value)
        )

    def test_form_class_over_a_form_validator(self) -> None:
        message = refusal(row_form_class_many_spec(FormValidator(RowFormClassBookRow)))
        assert message == (
            "SpecFormSetView sets form_class over a FormValidator, whose form is already the "
            "page's: drop form_class."
        )

    def test_a_row_form_the_wire_read_cannot_carry(self) -> None:
        message = refusal(row_form_class_many_spec(), SplitRow)
        assert message.startswith("SpecFormSetView.form_class SplitRow cannot be read off a post: ")

    def test_a_field_the_rows_do_not_declare(self) -> None:
        assert refusal(row_form_class_many_spec(), SubtitledRow) == (
            "SpecFormSetView.form_class SubtitledRow posts 'subtitle', which the spec does not "
            "declare: it would reach dispatch as an unknown argument."
        )

    def test_a_top_level_read_is_not_a_row_s_field(self) -> None:
        spec = row_form_class_many_spec(
            reads=Parameters.of(Parameter("shelf", "integer", required=True))
        )
        assert refusal(spec, ShelfRow) == (
            "SpecFormSetView.form_class ShelfRow posts 'shelf', which the spec does not "
            "declare: it would reach dispatch as an unknown argument."
        )

    def test_a_required_top_level_read_the_form_leaves_out_passes(self) -> None:
        spec = row_form_class_many_spec(
            reads=Parameters.of(Parameter("shelf", "integer", required=True))
        )
        row_form_class_page(spec)

    def test_a_required_row_parameter_the_form_leaves_out(self) -> None:
        assert refusal(row_form_class_many_spec(), PagesOnlyRow) == (
            "SpecFormSetView.form_class PagesOnlyRow does not post 'title', which each row "
            "requires, and the server does not fill it."
        )

    def test_an_optional_row_parameter_the_form_leaves_out_passes(self) -> None:
        row_form_class_page(row_form_class_many_spec(), TitleOnlyRow)

    def test_a_required_row_parameter_the_server_fills_passes(self) -> None:
        row_form_class_page(row_form_class_many_spec(input_data=server_title), PagesOnlyRow)

    def test_a_route_does_not_fill_a_row(self) -> None:
        message = refusal(row_form_class_many_spec(), PagesOnlyRow, route_kwargs=None)
        assert "does not post 'title', which each row requires" in message

    def test_a_field_whose_type_differs(self) -> None:
        assert refusal(row_form_class_many_spec(), FloatPagesRow) == (
            "SpecFormSetView.form_class FloatPagesRow posts 'pages' as number, and the spec "
            "declares integer."
        )

    def test_a_field_whose_required_differs(self) -> None:
        assert refusal(row_form_class_many_spec(), OptionalTitleRow) == (
            "SpecFormSetView.form_class OptionalTitleRow does not require 'title', and the spec "
            "does: the form and the spec must agree on whether it may be left out."
        )

    @pytest.mark.parametrize(
        ("form_class", "method"), [(CleanRow, "clean"), (CleanTitleRow, "clean_title")]
    )
    def test_a_form_with_its_own_clean(self, form_class: Any, method: str) -> None:
        assert refusal(row_form_class_many_spec(), form_class) == (
            f"SpecFormSetView.form_class {form_class.__name__} defines {method}(), which the "
            "page never runs: the rule belongs in the spec's Validator."
        )

    def test_a_validator_with_unsendable_fields(self) -> None:
        validator = Declared(Parameter("title", "string"), unsendable=frozenset({"cover"}))
        assert refusal(row_form_class_many_spec(validator), forms.Form) == (
            "SpecFormSetView's spec requires 'cover', which its Validator cannot declare, so "
            "no form can post it."
        )

    def test_a_dataclass_validator_s_rows_take_the_form(self) -> None:
        row_form_class_page(row_form_class_many_spec(DataclassValidator(Book)))


CHOICE = "Select a valid choice. That choice is not one of the available choices."


@dataclasses.dataclass
class EditedNote:
    title: str
    id: int | None = None
    DELETE: bool = False


@dataclasses.dataclass
class KeptNote:
    title: str
    id: int | None = None


@dataclasses.dataclass
class UnkeyedNote:
    title: str


@dataclasses.dataclass
class SpelledDelete:
    title: str
    id: int | None = None
    DELETE: str = ""


@dataclasses.dataclass
class RequiredDelete:
    title: str
    DELETE: bool
    id: int | None = None


class EditFormClassTitleRow(forms.Form):
    title = forms.CharField()


class KeyedRow(forms.Form):
    title = forms.CharField()
    id = forms.IntegerField(required=False)


def edit_form_class_edit_spec(row: type = EditedNote, **kwargs: Any) -> ServiceSpec:
    kwargs.setdefault("collection_selector_spec", OWN_NOTES)
    return ServiceSpec(
        service=Record(),
        validator=DataclassValidator(row),
        permissions=OPEN,
        many=True,
        **kwargs,
    )


def edit_form_class_page(spec: ServiceSpec, **initkwargs: Any) -> Any:
    return SpecFormSetView.as_view(
        spec=spec, form_class=EditFormClassTitleRow, success_url="/done/", **initkwargs
    )


def edit_form_class_get(view: Any, user: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user))


def edit_form_class_post(view: Any, user: Any, data: Mapping[str, Any]) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user))


def edit_form_class_sent(spec: ServiceSpec) -> list[dict[str, Any]]:
    return [dict(row) for row in spec.service.calls[0][spec.many_argument]]


@pytest.fixture
def edit_form_class_notes(ada: Any) -> list[Note]:
    return [Note.objects.create(owner=ada, title=title) for title in ("Emma", "Persuasion")]


class TestEditFormClassGet:
    def test_one_prefilled_form_a_row_then_the_extra_forms(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        formset = edit_form_class_get(
            edit_form_class_page(edit_form_class_edit_spec()), ada
        ).context_data["formset"]
        assert formset.initial_form_count() == 2
        assert len(formset.forms) == 3
        assert [form["title"].value() for form in formset.forms[:2]] == ["Emma", "Persuasion"]
        assert [form["id"].value() for form in formset.forms[:2]] == [
            n.pk for n in edit_form_class_notes
        ]
        assert "id" not in formset.forms[2].fields

    def test_the_management_form_carries_the_set_s_count(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        content = edit_form_class_get(
            edit_form_class_page(edit_form_class_edit_spec()), ada
        ).rendered_content
        assert 'name="form-INITIAL_FORMS" value="2"' in content
        assert f'name="form-0-id" value="{edit_form_class_notes[0].pk}"' in content

    def test_only_the_principal_s_set_is_shown(self, ada: Any, edit_form_class_notes: Any) -> None:
        Note.objects.create(owner=make_user("bob"), title="Not ada's")
        formset = edit_form_class_get(
            edit_form_class_page(edit_form_class_edit_spec()), ada
        ).context_data["formset"]
        assert formset.initial_form_count() == 2

    def test_a_delete_box_is_offered_where_the_rows_declare_one(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        formset = edit_form_class_get(
            edit_form_class_page(edit_form_class_edit_spec()), ada
        ).context_data["formset"]
        assert "DELETE" in formset.forms[0].fields

    def test_no_delete_box_where_the_rows_declare_none(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        formset = edit_form_class_get(
            edit_form_class_page(edit_form_class_edit_spec(KeptNote)), ada
        ).context_data["formset"]
        assert "DELETE" not in formset.forms[0].fields
        assert formset.forms[0]["id"].value() == edit_form_class_notes[0].pk


class TestEditFormClassPost:
    def test_each_initial_row_is_sent_with_its_key(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        spec = edit_form_class_edit_spec()
        data = {
            **management(3, 2),
            "form-0-id": str(edit_form_class_notes[0].pk),
            "form-0-title": "Emma!",
            "form-1-id": str(edit_form_class_notes[1].pk),
            "form-1-title": "Persuasion",
            "form-1-DELETE": "on",
            "form-2-title": "Sanditon",
        }
        assert edit_form_class_post(edit_form_class_page(spec), ada, data).status_code == 302
        assert edit_form_class_sent(spec) == [
            {"title": "Emma!", "id": edit_form_class_notes[0].pk, "DELETE": False},
            {"title": "Persuasion", "id": edit_form_class_notes[1].pk, "DELETE": True},
            {"title": "Sanditon", "id": None, "DELETE": False},
        ]

    def test_an_extra_form_ticked_for_deletion_sends_no_row(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        spec = edit_form_class_edit_spec()
        data = {
            **management(2, 1),
            "form-0-id": str(edit_form_class_notes[0].pk),
            "form-0-title": "Emma",
            "form-1-title": "Sanditon",
            "form-1-DELETE": "on",
        }
        assert edit_form_class_post(edit_form_class_page(spec), ada, data).status_code == 302
        assert edit_form_class_sent(spec) == [
            {"title": "Emma", "id": edit_form_class_notes[0].pk, "DELETE": False}
        ]

    def test_a_stray_delete_on_a_set_that_deletes_nothing_still_sends_the_row(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        spec = edit_form_class_edit_spec(KeptNote)
        data = {
            **management(2, 1),
            "form-0-id": str(edit_form_class_notes[0].pk),
            "form-0-title": "Emma",
            "form-1-title": "Sanditon",
            "form-1-DELETE": "on",
        }
        assert edit_form_class_post(edit_form_class_page(spec), ada, data).status_code == 302
        assert edit_form_class_sent(spec) == [
            {"title": "Emma", "id": edit_form_class_notes[0].pk},
            {"title": "Sanditon", "id": None},
        ]

    def test_a_row_left_out_is_not_sent(self, ada: Any, edit_form_class_notes: Any) -> None:
        spec = edit_form_class_edit_spec(KeptNote)
        data = {
            **management(1, 1),
            "form-0-id": str(edit_form_class_notes[1].pk),
            "form-0-title": "P",
        }
        assert edit_form_class_post(edit_form_class_page(spec), ada, data).status_code == 302
        assert edit_form_class_sent(spec) == [{"title": "P", "id": edit_form_class_notes[1].pk}]

    def test_a_key_outside_the_set_is_refused_on_its_row(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        theirs = Note.objects.create(owner=make_user("bob"), title="Not ada's")
        spec = edit_form_class_edit_spec()
        data = {**management(1, 1), "form-0-id": str(theirs.pk), "form-0-title": "Mine"}
        response = edit_form_class_post(edit_form_class_page(spec), ada, data)
        assert response.status_code == 400
        assert dict(response.context_data["formset"].forms[0].errors) == {"id": [CHOICE]}
        assert spec.service.calls == []

    def test_an_initial_form_without_its_key_is_refused(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        spec = edit_form_class_edit_spec()
        data = {**management(1, 1), "form-0-title": "Mine"}
        response = edit_form_class_post(edit_form_class_page(spec), ada, data)
        assert response.status_code == 400
        assert dict(response.context_data["formset"].forms[0].errors) == {
            "id": ["This field is required."]
        }

    def test_a_post_whose_set_went_unresolved_builds_no_key(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        spec = edit_form_class_edit_spec(collection_selector_spec=FROM_FIRST)
        data = {
            **management(1, 1),
            "form-0-id": str(edit_form_class_notes[0].pk),
            "form-0-title": "Emma",
        }
        response = edit_form_class_post(edit_form_class_page(spec), ada, data)
        assert response.status_code == 400
        assert set(response.context_data["formset"].forms[0].fields) == {"title", "DELETE"}
        assert spec.service.calls == []


class TestRefused:
    def test_rows_that_declare_no_key_are_refused_when_the_set_is_built(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        view = edit_form_class_page(edit_form_class_edit_spec(UnkeyedNote))
        with pytest.raises(ImproperlyConfigured) as refused:
            edit_form_class_get(view, ada)
        assert str(refused.value) == (
            "SpecFormSetView edits the rows its spec's collection_selector_spec resolves, "
            "each sent with its key 'id', which the spec's rows do not declare."
        )

    def test_a_delete_the_rows_declare_as_anything_but_a_boolean(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            edit_form_class_page(edit_form_class_edit_spec(SpelledDelete))
        assert str(refused.value) == (
            "SpecFormSetView edits a set, whose deleted rows carry 'DELETE' as a boolean, and "
            "the spec's rows declare string."
        )

    def test_a_delete_on_a_page_that_edits_no_set_is_any_row_field(self) -> None:
        edit_form_class_page(
            edit_form_class_edit_spec(SpelledDelete, collection_selector_spec=None)
        )

    def test_a_required_delete_is_the_formset_s_to_post(self) -> None:
        edit_form_class_page(edit_form_class_edit_spec(RequiredDelete))

    def test_a_required_delete_on_a_page_that_edits_no_set_is_the_form_s(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="does not post 'DELETE'"):
            edit_form_class_page(
                edit_form_class_edit_spec(RequiredDelete, collection_selector_spec=None)
            )


class TestAddingARow:
    def page(self) -> Any:
        return edit_form_class_page(
            edit_form_class_edit_spec(), commit_button="save", add_button="add"
        )

    def data(self, edit_form_class_notes: list[Note]) -> dict[str, str]:
        return {
            **management(3, 2),
            "form-0-id": str(edit_form_class_notes[0].pk),
            "form-0-title": "Emma",
            "form-1-id": str(edit_form_class_notes[1].pk),
            "form-1-title": "Persuasion",
            "add": "",
        }

    def test_the_set_comes_back_keyed_with_one_more_form(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        formset = edit_form_class_post(
            self.page(), ada, self.data(edit_form_class_notes)
        ).context_data["formset"]
        assert len(formset.forms) == 4
        assert [form["id"].value() for form in formset.forms[:2]] == [
            str(n.pk) for n in edit_form_class_notes
        ]
        assert "id" not in formset.forms[3].fields

    def test_an_htmx_add_answers_the_new_form_unkeyed(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        request = FACTORY.post(
            "/",
            data=self.data(edit_form_class_notes),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="spec-form-set-rows",
        )
        response = self.page()(signed_in(request, ada))
        content = response.content.decode()
        assert 'name="form-3-title"' in content
        assert "form-3-id" not in content


class TestARowFormDeclaringTheKey:
    def data(self, edit_form_class_notes: list[Note], **extra: str) -> dict[str, str]:
        return {
            **management(3, 2),
            "form-0-id": str(edit_form_class_notes[0].pk),
            "form-0-title": "Emma",
            "form-1-id": str(edit_form_class_notes[1].pk),
            "form-1-title": "Persuasion",
            "form-2-title": "Hijack",
            **extra,
        }

    def view(self, spec: ServiceSpec) -> Any:
        return SpecFormSetView.as_view(spec=spec, form_class=KeyedRow, success_url="/done/")

    def test_an_extra_form_naming_a_key_outside_the_set_is_refused_on_its_row(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        theirs = Note.objects.create(owner=make_user("bob"), title="Not ada's")
        spec = edit_form_class_edit_spec()
        response = edit_form_class_post(
            self.view(spec), ada, self.data(edit_form_class_notes, **{"form-2-id": str(theirs.pk)})
        )
        assert response.status_code == 400
        assert dict(response.context_data["formset"].forms[2].errors) == {"id": [CHOICE]}
        assert spec.service.calls == []

    def test_an_extra_form_with_an_empty_key_still_creates(
        self, ada: Any, edit_form_class_notes: Any
    ) -> None:
        spec = edit_form_class_edit_spec()
        assert (
            edit_form_class_post(
                self.view(spec), ada, self.data(edit_form_class_notes, **{"form-2-id": ""})
            ).status_code
            == 302
        )
        assert edit_form_class_sent(spec)[2] == {"title": "Hijack", "id": None, "DELETE": False}
