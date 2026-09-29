"""``add_argument_errors``: a refusal tree placed on a bound form, beside its own errors, once.

Every form here is bound and never validated before the call, as a view hands
one over: the form's own errors are whatever its ``full_clean()`` finds when
``form.errors`` is first read, which is what the placement reads to know what
the form already says.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy

from django_service_specs.http.add_argument_errors import add_argument_errors

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
        # What a service re-raising a model's ``message_dict`` carries, since
        # Django keeps a model's form-wide messages under ``"__all__"``.
        form = bound(title="Hi")
        add_argument_errors(form, {"__all__": ["Closed today."]})
        assert form.non_field_errors() == ["Closed today."]

    def test_a_key_the_form_has_no_field_for_is_prefixed_with_it(self) -> None:
        # A URL kwarg, or a key a service chose: the form has nowhere to show
        # it but its own errors, and the prefix says what it is about.
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
        # FormValidator refuses with the form's own messages, so a view's
        # bound form carries each of them before the tree is placed.
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
        # The shape check refuses a multi-select's element at its index, in the
        # words the form uses for the same value. The index is dropped on a
        # field, which a person reads as one control, so the two are one message.
        refused = "Select a valid choice. c is not one of the available choices."
        form = bound(title="Hi", tags=["a", "c"])
        add_argument_errors(form, {"tags": {1: [refused]}})
        assert errors(form) == {"tags": [refused]}


class TestMessages:
    def test_a_string_is_one_message_not_its_characters(self) -> None:
        form = bound(title="Hi")
        add_argument_errors(form, {"title": "Taken.", "non_field_errors": "Closed today."})
        assert errors(form) == {"title": ["Taken."], "__all__": ["Closed today."]}

    def test_a_lazy_message_is_one_message_not_its_characters(self) -> None:
        # A lazy translation is not a ``str``, so a ladder asking "is it a
        # string?" and iterating the rest would take it apart.
        form = bound(title="Hi")
        add_argument_errors(form, {"non_field_errors": gettext_lazy("Closed today.")})
        assert form.non_field_errors() == ["Closed today."]
