from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, TypedDict

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import HttpResponse
from django.urls import path
from django_service_specs import (
    NON_FIELD_ERRORS,
    DataclassValidator,
    FormValidator,
    InvalidArguments,
    Parameter,
    Parameters,
    ServiceSpec,
    Validated,
    ValidationContext,
    Validator,
)

from django_spec_html.views.spec_form_set_view import SpecFormSetView
from tests.dispatch.utils import OPEN, Record, make_user
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

TAKEN = "That title is taken."
REPEATED = "Two rows carry one title."
NEVER = "the form's own validator ran"


@dataclasses.dataclass
class Book:
    title: str
    pages: int | None = None


class BookRow(forms.Form):
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


class Titled(TypedDict):
    title: str


def server_title() -> Titled:
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


def many_spec(validator: Any = None, **kwargs: Any) -> ServiceSpec:
    return ServiceSpec(
        service=Record(),
        validator=Shelving() if validator is None else validator,
        permissions=OPEN,
        many=True,
        **kwargs,
    )


def page(spec: ServiceSpec, form_class: Any = BookRow, **initkwargs: Any) -> Any:
    return SpecFormSetView.as_view(
        spec=spec, form_class=form_class, success_url="/done/", **initkwargs
    )


def refusal(spec: ServiceSpec, form_class: Any = BookRow, **initkwargs: Any) -> str:
    with pytest.raises(ImproperlyConfigured) as refused:
        page(spec, form_class, **initkwargs)
    return str(refused.value)


def management(total: int) -> dict[str, str]:
    return {"form-TOTAL_FORMS": str(total), "form-INITIAL_FORMS": "0"}


def post(view: Any, user: Any, data: Mapping[str, Any]) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user))


def errors(response: Any) -> list[dict[str, list[str]]]:
    return [
        {key: list(messages) for key, messages in form.errors.items()}
        for form in response.context_data["formset"].forms
    ]


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


class TestAFormClassOverANonFormValidator:
    def test_get_renders_a_formset_of_the_form_class(self, ada: Any) -> None:
        response = page(many_spec())(signed_in(FACTORY.get("/"), ada))
        formset = response.context_data["formset"]
        assert response.status_code == 200
        assert type(formset.forms[0]) is BookRow
        assert 'name="form-0-title"' in response.rendered_content

    def test_a_valid_post_sends_the_filled_rows_and_redirects(self, ada: Any) -> None:
        spec = many_spec()
        data = {**management(3), "form-0-title": "Emma", "form-2-title": "Persuasion"}
        response = post(page(spec, extra=3), ada, data)
        assert response.status_code == 302
        assert spec.service.calls[0][spec.many_argument] == [
            {"title": "Emma"},
            {"title": "Persuasion"},
        ]

    def test_a_row_refusal_lands_on_the_form_that_sent_the_row(self, ada: Any) -> None:
        data = {**management(3), "form-1-title": "Emma", "form-2-title": "taken"}
        response = post(page(many_spec(), extra=3), ada, data)
        assert response.status_code == 400
        assert errors(response) == [{}, {}, {"title": [TAKEN]}]

    def test_a_set_wide_refusal_is_the_formset_s_non_form_errors(self, ada: Any) -> None:
        data = {**management(2), "form-0-title": "Emma", "form-1-title": "Emma"}
        response = post(page(many_spec()), ada, data)
        assert list(response.context_data["formset"].non_form_errors()) == [REPEATED]
        assert errors(response) == [{}, {}]


class TestTheRowFormRendersAndIsRead:
    def test_its_validators_never_run_on_a_success(self, ada: Any) -> None:
        spec = many_spec()
        data = {**management(1), "form-0-title": "Emma", "form-0-pages": "3"}
        response = post(page(spec, GuardedRow), ada, data)
        assert response.status_code == 302
        assert spec.service.calls[0][spec.many_argument] == [{"title": "Emma", "pages": 3}]

    def test_a_refusal_carries_the_validator_s_errors_alone(self, ada: Any) -> None:
        data = {**management(2), "form-0-title": "Emma", "form-1-title": "taken"}
        response = post(page(many_spec(), GuardedRow), ada, data)
        assert errors(response) == [{}, {"title": [TAKEN]}]

    def test_a_set_wide_refusal_runs_no_row_validator(self, ada: Any) -> None:
        data = {**management(2), "form-0-title": "Emma", "form-1-title": "Emma"}
        response = post(page(many_spec(), GuardedRow), ada, data)
        assert errors(response) == [{}, {}]


class OwnedRow(BookRow):
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
        view = Owned.as_view(spec=many_spec(), form_class=OwnedRow, success_url="/done/", extra=2)
        response = view(signed_in(FACTORY.get("/"), ada))
        formset = response.context_data["formset"]
        assert [form.shelf for form in formset.forms] == ["fiction", "fiction"]
        assert formset.empty_form.shelf == "fiction"

    @pytest.mark.parametrize("key", ["data", "files", "instance"])
    def test_a_keyword_the_page_sets_is_refused(self, ada: Any, key: str) -> None:
        class Overreaching(SpecFormSetView):
            def get_form_kwargs(self) -> Mapping[str, Any]:
                return {key: None}

        view = Overreaching.as_view(spec=many_spec(), form_class=BookRow, success_url="/done/")
        with pytest.raises(ImproperlyConfigured, match=f"returned '{key}', which the page sets"):
            view(signed_in(FACTORY.get("/"), ada))

    def test_a_form_validator_page_never_asks_them(self, ada: Any) -> None:
        class Asked(SpecFormSetView):
            def get_form_kwargs(self) -> Mapping[str, Any]:
                raise AssertionError("asked")

        view = Asked.as_view(spec=many_spec(FormValidator(BookRow)), success_url="/done/")
        assert view(signed_in(FACTORY.get("/"), ada)).status_code == 200


class SplitRow(forms.Form):
    when = forms.SplitDateTimeField()


class SubtitledRow(BookRow):
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


class CleanRow(BookRow):
    def clean(self) -> dict[str, Any]:
        return super().clean() or {}


class CleanTitleRow(BookRow):
    def clean_title(self) -> str:
        return self.cleaned_data["title"]


class ShelfRow(BookRow):
    shelf = forms.IntegerField()


class TestAsViewRefusesPerRow:
    def test_a_validator_not_a_form_validator_without_form_class(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecFormSetView.as_view(spec=many_spec(), success_url="/done/")
        assert "set form_class, or the spec's validator must be a FormValidator; got Shelving" in (
            str(refused.value)
        )

    def test_form_class_over_a_form_validator(self) -> None:
        message = refusal(many_spec(FormValidator(BookRow)))
        assert message == (
            "SpecFormSetView sets form_class over a FormValidator, whose form is already the "
            "page's: drop form_class."
        )

    def test_a_row_form_the_wire_read_cannot_carry(self) -> None:
        message = refusal(many_spec(), SplitRow)
        assert message.startswith("SpecFormSetView.form_class SplitRow cannot be read off a post: ")

    def test_a_field_the_rows_do_not_declare(self) -> None:
        assert refusal(many_spec(), SubtitledRow) == (
            "SpecFormSetView.form_class SubtitledRow posts 'subtitle', which the spec does not "
            "declare: it would reach dispatch as an unknown argument."
        )

    def test_a_top_level_read_is_not_a_row_s_field(self) -> None:
        spec = many_spec(reads=Parameters.of(Parameter("shelf", "integer", required=True)))
        assert refusal(spec, ShelfRow) == (
            "SpecFormSetView.form_class ShelfRow posts 'shelf', which the spec does not "
            "declare: it would reach dispatch as an unknown argument."
        )

    def test_a_required_top_level_read_the_form_leaves_out_passes(self) -> None:
        spec = many_spec(reads=Parameters.of(Parameter("shelf", "integer", required=True)))
        page(spec)

    def test_a_required_row_parameter_the_form_leaves_out(self) -> None:
        assert refusal(many_spec(), PagesOnlyRow) == (
            "SpecFormSetView.form_class PagesOnlyRow does not post 'title', which each row "
            "requires, and the server does not fill it."
        )

    def test_an_optional_row_parameter_the_form_leaves_out_passes(self) -> None:
        page(many_spec(), TitleOnlyRow)

    def test_a_required_row_parameter_the_server_fills_passes(self) -> None:
        page(many_spec(input_data=server_title), PagesOnlyRow)

    def test_a_route_does_not_fill_a_row(self) -> None:
        message = refusal(many_spec(), PagesOnlyRow, route_kwargs=None)
        assert "does not post 'title', which each row requires" in message

    def test_a_field_whose_type_differs(self) -> None:
        assert refusal(many_spec(), FloatPagesRow) == (
            "SpecFormSetView.form_class FloatPagesRow posts 'pages' as number, and the spec "
            "declares integer."
        )

    def test_a_field_whose_required_differs(self) -> None:
        assert refusal(many_spec(), OptionalTitleRow) == (
            "SpecFormSetView.form_class OptionalTitleRow does not require 'title', and the spec "
            "does: the form and the spec must agree on whether it may be left out."
        )

    @pytest.mark.parametrize(
        ("form_class", "method"), [(CleanRow, "clean"), (CleanTitleRow, "clean_title")]
    )
    def test_a_form_with_its_own_clean(self, form_class: Any, method: str) -> None:
        assert refusal(many_spec(), form_class) == (
            f"SpecFormSetView.form_class {form_class.__name__} defines {method}(), which the "
            "page never runs: the rule belongs in the spec's Validator."
        )

    def test_a_validator_with_unsendable_fields(self) -> None:
        validator = Declared(Parameter("title", "string"), unsendable=frozenset({"cover"}))
        assert refusal(many_spec(validator), forms.Form) == (
            "SpecFormSetView's spec requires 'cover', which its Validator cannot declare, so "
            "no form can post it."
        )

    def test_a_dataclass_validator_s_rows_take_the_form(self) -> None:
        page(many_spec(DataclassValidator(Book)))
