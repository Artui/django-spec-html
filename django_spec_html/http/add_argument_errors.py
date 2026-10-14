"""``add_argument_errors`` - a refusal tree placed on a bound form, beside the form's own errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import NON_FIELD_ERRORS as FORM_NON_FIELD_ERRORS
from django.forms import BaseForm

from django_service_specs.parameters.utils import NON_FIELD_KEYS, refusal_messages
from django_service_specs.relations.utils import clean_held_to_their_columns


def add_argument_errors(form: BaseForm, detail: Mapping[Any, Any]) -> None:
    """Place a refusal tree on a bound ``form``, beside the form's own errors, never twice.

    ``detail`` is an
    [`InvalidArguments`][django_service_specs.parameters.invalid_arguments.InvalidArguments]
    tree, or a ``ServiceValidationError``'s mapping. Each message goes where a
    person reading the form looks for it, through Django's public
    ``form.add_error``:

    - **A key naming one of the form's fields** puts its messages on that
      field. A form is flat, so the only tree below a field is an array's
      element indices, which mean nothing to a person reading one control: the
      messages go on the field without them.
    - ``non_field_errors``, and Django's own ``"__all__"``, go to the form's
      non-field errors as they are.
    - **Any other key** - a URL kwarg such as ``pk``, or a key a service chose -
      goes to the non-field errors prefixed with its path, ``"pk: ..."``, so the
      reader knows what it is about. A nested tree is flattened to its leaves
      with the path joined by dots, ``"books.1.title: ..."``, a row's own
      ``non_field_errors`` adding nothing to the path.

    **Never a duplicate.** A message the form already carries at the same place
    is not added again. The form validating the same data says the same thing
    the kernel does - a ``FormValidator``'s refusal *is* the form's errors, and
    the shape check spells its messages as Django's fields do - so without this
    most refusals would show twice. A form not yet validated is validated
    first, as reading ``form.errors`` would, so the form's own errors are
    always the ones compared against; but with the guards a
    ``FormValidator``'s form is validated with, since the form's own
    validation would hand the database driver a number no integer column
    holds, which SQLite's refuses with ``OverflowError``. A model choice
    field's key past its column's range is refused as ``invalid_choice`` and
    a ``ModelForm``'s value past its integer column's range as ``max_value``
    or ``min_value``, in the words a ``FormValidator`` refuses them with, so
    the refusal of one is placed once
    (test_a_list_s_key_past_its_column_is_refused_once,
    test_a_model_form_s_integer_past_its_column_is_refused_once). A form
    already validated is compared as it stands.

    A leaf is a message whatever its type: a string, a lazy translation (which
    is not a ``str``, and is never taken apart into characters), or a list of
    either. The form must be bound, as Django's ``add_error`` requires.
    """
    # Django's own marker of a form not yet validated, which the stubs declare
    # on no form, hence ``Any``. Each way is held by its own test:
    # test_a_list_s_key_past_its_column_is_refused_once (not yet validated,
    # so validated here with the guards) and
    # test_a_form_already_validated_is_compared_as_it_stands (an error state
    # the caller already holds, which validating here would start over).
    bound: Any = form
    if bound._errors is None:
        clean_held_to_their_columns(form)
    for key, value in detail.items():
        field = key if key in form.fields else None
        for path, message in refusal_messages(value):
            if field is not None:
                _add_once(form, field, message)
                continue
            where = path if key in NON_FIELD_KEYS else (str(key), *path)
            _add_once(form, None, f"{'.'.join(where)}: {message}" if where else message)


def _add_once(form: BaseForm, field: str | None, message: str) -> None:
    """``form.add_error``, unless the place already carries ``message``."""
    # An ErrorList yields its messages as strings, so ``in`` compares a string
    # with each, and a missing key reads as a place carrying nothing yet.
    if message not in form.errors.get(FORM_NON_FIELD_ERRORS if field is None else field, ()):
        form.add_error(field, message)
