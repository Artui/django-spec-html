"""``add_argument_errors`` - a refusal tree placed on a bound form, beside the form's own errors."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Final

from django.core.exceptions import NON_FIELD_ERRORS as FORM_NON_FIELD_ERRORS
from django.forms import BaseForm

from django_service_specs.parameters.utils import NON_FIELD_ERRORS

_NON_FIELD_KEYS: Final = (NON_FIELD_ERRORS, FORM_NON_FIELD_ERRORS)
"""The keys a message about the whole input sits under: the kernel's, and Django's.

Django's is here because a service re-raising a model's ``message_dict`` as a
``ServiceValidationError`` carries its form-wide messages under ``"__all__"``.
Each is held by its own test: test_non_field_errors_go_to_the_forms_own and
test_djangos_own_non_field_key_is_read_the_same."""


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
    most refusals would show twice. Reading ``form.errors`` runs the form's
    ``full_clean()`` first, as Django documents for ``add_error`` called from a
    view, so the form's own errors are always the ones compared against.

    A leaf is a message whatever its type: a string, a lazy translation (which
    is not a ``str``, and is never taken apart into characters), or a list of
    either. The form must be bound, as Django's ``add_error`` requires.
    """
    for key, value in detail.items():
        field = key if key in form.fields else None
        for path, message in _leaves(value, ()):
            if field is not None:
                _add_once(form, field, message)
                continue
            where = path if key in _NON_FIELD_KEYS else (str(key), *path)
            _add_once(form, None, f"{'.'.join(where)}: {message}" if where else message)


def _leaves(value: Any, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], str]]:
    """Every message below ``value``, each with its path from where the walk began."""
    if isinstance(value, Mapping):
        for key, inner in value.items():
            yield from _leaves(inner, path if key in _NON_FIELD_KEYS else (*path, str(key)))
    elif isinstance(value, list):
        # A list is the tree's leaf: its messages share the path. Only a list,
        # as ``error_response`` reads a service's detail: anything else is one
        # message, and a lazy translation is not a ``str``.
        for inner in value:
            yield from _leaves(inner, path)
    else:
        yield path, str(value)


def _add_once(form: BaseForm, field: str | None, message: str) -> None:
    """``form.add_error``, unless the place already carries ``message``."""
    # An ErrorList yields its messages as strings, so ``in`` compares a string
    # with each, and a missing key reads as a place carrying nothing yet.
    if message not in form.errors.get(FORM_NON_FIELD_ERRORS if field is None else field, ()):
        form.add_error(field, message)
