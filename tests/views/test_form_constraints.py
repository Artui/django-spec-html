from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    Parameter,
    Parameters,
    ServiceSpec,
    ValidationContext,
    Validator,
    string_lengths,
)

from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from tests.dispatch.utils import OPEN, Record

TIGHT = "The form only renders, so the spec must be at least as tight as the form."


class Declared(Validator):
    def __init__(self, *declared: Parameter) -> None:
        self.declared = Parameters.of(*declared)

    def parameters(self) -> Parameters:
        return self.declared

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)


class LongTitleForm(forms.Form):
    title = forms.CharField(max_length=100)


class ShortTitleForm(forms.Form):
    title = forms.CharField(min_length=3)


class OptionalShortTitleForm(forms.Form):
    title = forms.CharField(min_length=3, required=False)


class BoundedPagesForm(forms.Form):
    pages = forms.IntegerField(min_value=1, max_value=10)


class PositivePagesForm(forms.Form):
    pages = forms.IntegerField(min_value=0)


class ColourForm(forms.Form):
    colour = forms.ChoiceField(choices=[("red", "Red"), ("blue", "Blue")])


class ColoursForm(forms.Form):
    colours = forms.MultipleChoiceField(choices=[("red", "Red"), ("blue", "Blue")])


def string(name: str, **kwargs: Any) -> Parameter:
    return Parameter(name, "string", required=True, **kwargs)


def integer(name: str, **kwargs: Any) -> Parameter:
    return Parameter(name, "integer", required=True, **kwargs)


def spec(*declared: Parameter, many: bool = False) -> ServiceSpec:
    return ServiceSpec(service=Record(), validator=Declared(*declared), permissions=OPEN, many=many)


def page(form_class: Any, *declared: Parameter) -> Any:
    return SpecFormView.as_view(
        spec=spec(*declared),
        form_class=form_class,
        template_name="spec_form.html",
        success_url="/done/",
    )


def rows(form_class: Any, *declared: Parameter) -> Any:
    return SpecFormSetView.as_view(
        spec=spec(*declared, many=True), form_class=form_class, success_url="/done/"
    )


def refusal(form_class: Any, *declared: Parameter) -> str:
    with pytest.raises(ImproperlyConfigured) as refused:
        page(form_class, *declared)
    return str(refused.value)


def row_refusal(form_class: Any, *declared: Parameter) -> str:
    with pytest.raises(ImproperlyConfigured) as refused:
        rows(form_class, *declared)
    return str(refused.value)


def held(form_class: str, name: str, stated: str, declared: str, view: str = "SpecFormView") -> str:
    return (
        f"{view}.form_class {form_class} posts {name!r}: the form {stated}, and the spec "
        f"declares {declared}. {TIGHT}"
    )


MAX_100 = "states a maximum length of 100"
MIN_3 = "states a minimum length of 3"
COLOURS = "takes the choices ['red', 'blue']"


class TestAMaximumLength:
    def test_the_spec_declaring_none_is_refused(self) -> None:
        assert refusal(LongTitleForm, string("title")) == held(
            "LongTitleForm", "title", MAX_100, "none"
        )

    def test_a_looser_spec_is_refused(self) -> None:
        declared = string("title", json_schema_extra=string_lengths(max_length=200))
        assert refusal(LongTitleForm, declared) == held("LongTitleForm", "title", MAX_100, "200")

    @pytest.mark.parametrize("limit", [100, 50])
    def test_an_equal_or_tighter_spec_passes(self, limit: int) -> None:
        page(LongTitleForm, string("title", json_schema_extra=string_lengths(max_length=limit)))


class TestAMinimumLength:
    def test_the_spec_declaring_none_is_refused(self) -> None:
        assert refusal(ShortTitleForm, string("title")) == held(
            "ShortTitleForm", "title", MIN_3, "none"
        )

    def test_a_looser_spec_is_refused(self) -> None:
        declared = string("title", json_schema_extra=string_lengths(min_length=1))
        assert refusal(ShortTitleForm, declared) == held("ShortTitleForm", "title", MIN_3, "1")

    @pytest.mark.parametrize("limit", [3, 5])
    def test_an_equal_or_tighter_spec_passes(self, limit: int) -> None:
        page(ShortTitleForm, string("title", json_schema_extra=string_lengths(min_length=limit)))

    def test_an_optional_field_states_no_minimum(self) -> None:
        page(OptionalShortTitleForm, Parameter("title", "string"))


class TestNumericBounds:
    def test_a_positive_integer_field_over_a_plain_integer_passes(self) -> None:
        page(PositivePagesForm, integer("pages"))

    def test_bounds_the_spec_does_not_declare_pass_on_a_row_form(self) -> None:
        rows(BoundedPagesForm, integer("pages"))


class TestChoices:
    def test_the_spec_declaring_none_is_refused(self) -> None:
        assert refusal(ColourForm, string("colour")) == held(
            "ColourForm", "colour", COLOURS, "none"
        )

    def test_a_spec_taking_a_choice_the_form_lacks_is_refused(self) -> None:
        declared = string("colour", choices=("red", "blue", "green"))
        assert refusal(ColourForm, declared) == held(
            "ColourForm", "colour", COLOURS, "['red', 'blue', 'green']"
        )

    @pytest.mark.parametrize("choices", [("blue", "red"), ("red",)])
    def test_the_same_or_fewer_choices_pass(self, choices: tuple[str, ...]) -> None:
        page(ColourForm, string("colour", choices=choices))

    def test_an_array_s_element_choices_are_held_the_same(self) -> None:
        declared = Parameter("colours", "array", required=True, items="string")
        assert refusal(ColoursForm, declared) == held("ColoursForm", "colours", COLOURS, "none")


class TestAFormSetRowForm:
    def test_a_maximum_length_the_spec_lacks_is_refused(self) -> None:
        assert row_refusal(LongTitleForm, string("title")) == held(
            "LongTitleForm", "title", MAX_100, "none", view="SpecFormSetView"
        )

    def test_choices_the_spec_lacks_are_refused(self) -> None:
        assert row_refusal(ColourForm, string("colour")) == held(
            "ColourForm", "colour", COLOURS, "none", view="SpecFormSetView"
        )

    def test_a_tighter_spec_passes(self) -> None:
        rows(LongTitleForm, string("title", json_schema_extra=string_lengths(max_length=10)))
