from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import NON_FIELD_ERRORS as FORM_NON_FIELD_ERRORS
from django.forms import BaseForm
from django_service_specs import NON_FIELD_KEYS, clean_held_to_their_columns, refusal_messages


def add_argument_errors(form: BaseForm, detail: Mapping[Any, Any]) -> None:
    """Place a refusal tree on a bound ``form``, beside the form's own errors, never twice."""
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
    if message not in form.errors.get(FORM_NON_FIELD_ERRORS if field is None else field, ()):
        form.add_error(field, message)
