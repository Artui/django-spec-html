from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.utils.translation import gettext_lazy

from django_spec_html.http.add_argument_errors import add_argument_errors
from tests.adapter_app.models import Author, Book, Edition

REQUIRED = "This field is required."
CLOSED = "Entries are closed."


class EntryForm(forms.Form):
    title = forms.CharField(max_length=5)
    tags = forms.MultipleChoiceField(choices=[("a", "A"), ("b", "B")], required=False)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("title") == "shut":
            raise forms.ValidationError(CLOSED)
        return cleaned


def bound(**data: Any) -> EntryForm:
    return EntryForm(data=data)


def errors(form: forms.BaseForm) -> dict[str, list[str]]:
    """The form's errors as a person reads them: Django's key for the form-wide ones."""
    return {key: list(messages) for key, messages in form.errors.items()}


class TestPlacement:
    def test_a_key_naming_a_field_goes_on_that_field(self) -> None:
        form = bound(title="Hi")
        add_argument_errors(form, {"title": ["Taken."]})
        assert errors(form) == {"title": ["Taken."]}

    def test_non_field_errors_go_to_the_forms_own(self) -> None:
        form = bound(title="Hi")
        add_argument_errors(form, {"non_field_errors": ["Closed today."]})
        assert form.non_field_errors() == ["Closed today."]

    def test_djangos_own_non_field_key_is_read_the_same(self) -> None:
        # A service re-raising message_dict: Django keeps form-wide messages under "__all__".
        form = bound(title="Hi")
        add_argument_errors(form, {"__all__": ["Closed today."]})
        assert form.non_field_errors() == ["Closed today."]

    def test_a_key_the_form_has_no_field_for_is_prefixed_with_it(self) -> None:
        # A URL kwarg or a service's key: the form has nowhere to show it but its own errors.
        form = bound(title="Hi")
        add_argument_errors(form, {"pk": ["Enter a whole number."]})
        assert errors(form) == {"__all__": ["pk: Enter a whole number."]}

    def test_a_nested_tree_is_flattened_with_its_path(self) -> None:
        form = bound(title="Hi")
        add_argument_errors(
            form,
            {"books": {1: {"title": [REQUIRED], "non_field_errors": ["Not a row."]}}},
        )
        # A row's own message is about the row, so its key is no part of the path.
        assert form.non_field_errors() == ["books.1.title: " + REQUIRED, "books.1: Not a row."]

    def test_the_forms_own_errors_stay_beside_the_placed_ones(self) -> None:
        form = bound(title="Longer")
        add_argument_errors(form, {"title": ["Taken."]})
        assert errors(form) == {
            "title": ["Ensure this value has at most 5 characters (it has 6).", "Taken."]
        }


class TestNeverADuplicate:
    def test_a_fields_message_the_form_already_carries_is_not_added(self) -> None:
        # FormValidator refuses with the form's own messages, which the view's bound form carries.
        form = bound(title="")
        add_argument_errors(form, {"title": [REQUIRED]})
        assert errors(form) == {"title": [REQUIRED]}

    def test_a_form_wide_message_the_form_already_carries_is_not_added(self) -> None:
        form = bound(title="shut")
        add_argument_errors(form, {"non_field_errors": [CLOSED]})
        assert form.non_field_errors() == [CLOSED]

    def test_a_message_repeated_in_the_tree_is_placed_once(self) -> None:
        form = bound(title="Hi")
        add_argument_errors(form, {"title": ["Taken.", "Taken."], "pk": ["Gone.", "Gone."]})
        assert errors(form) == {"title": ["Taken."], "__all__": ["pk: Gone."]}

    def test_an_array_elements_refusal_goes_on_its_field_once(self) -> None:
        # The shape check refuses a multi-select's element at its index, in the form's own words.
        refused = "Select a valid choice. c is not one of the available choices."
        form = bound(title="Hi", tags=["a", "c"])
        add_argument_errors(form, {"tags": {1: [refused]}})
        assert errors(form) == {"tags": [refused]}


class TestAFormAlreadyValidated:
    def test_a_form_already_validated_is_compared_as_it_stands(self) -> None:
        # A validated form and a message of the view's own: the form's errors are its state.
        form = bound(title="Hi")
        assert form.is_valid()
        form.add_error(None, "Added by the view.")
        add_argument_errors(form, {"title": ["Taken."]})
        assert errors(form) == {"__all__": ["Added by the view."], "title": ["Taken."]}


class TestMessages:
    def test_a_string_is_one_message_not_its_characters(self) -> None:
        form = bound(title="Hi")
        add_argument_errors(form, {"title": "Taken.", "non_field_errors": "Closed today."})
        assert errors(form) == {"title": ["Taken."], "__all__": ["Closed today."]}

    def test_a_lazy_message_is_one_message_not_its_characters(self) -> None:
        # A lazy translation is not a str, so a ladder asking "is it a string?" would iterate it.
        form = bound(title="Hi")
        add_argument_errors(form, {"non_field_errors": gettext_lazy("Closed today.")})
        assert form.non_field_errors() == ["Closed today."]


class AssigneesForm(forms.Form):
    """A form choosing several rows, which Django matches with one ``in`` lookup."""

    assignees = forms.ModelMultipleChoiceField(queryset=get_user_model().objects.all())


class EditionForm(forms.ModelForm):
    """A row whose integer column a constraint holds unique, so validating it queries."""

    class Meta:
        model = Edition
        fields = ["book", "number"]


@pytest.mark.django_db
class TestAValuePastItsColumn:
    """A form whose own validation would hand the driver a number no column holds."""

    PAST_KEY = f"Select a valid choice. {2**63} is not one of the available choices."

    @pytest.mark.parametrize("prefix", [None, "assign"], ids=["unprefixed", "prefixed"])
    def test_a_list_s_key_past_its_column_is_refused_once(self, prefix: str | None) -> None:
        # Bound as a view binds request.POST, a QueryDict whose item is its key's last value.
        name = "assignees" if prefix is None else f"{prefix}-assignees"
        data = QueryDict(f"{name}={2**63}&{name}=1")
        form = AssigneesForm(data=data, prefix=prefix)
        add_argument_errors(form, {"assignees": [self.PAST_KEY]})
        assert errors(form) == {"assignees": [self.PAST_KEY]}

    @pytest.mark.usefixtures("unstated_ranges")
    def test_a_model_form_s_integer_past_its_column_is_refused_once(self) -> None:
        book = Book.objects.create(
            author=Author.objects.create(name="Ada"), title="Notes", price=Decimal("1.00")
        )
        past = "Ensure this value is less than or equal to %s." % (2**63 - 1)
        form = EditionForm(data={"book": book.pk, "number": 2**63})
        add_argument_errors(form, {"number": [past]})
        assert errors(form) == {"number": [past]}
