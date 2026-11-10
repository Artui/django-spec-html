from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.apps import apps
from django.db import connection, models


def _forget_range_validators() -> None:
    # A field caches its range in its validators, so a range stated leaks into the next test.
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if isinstance(field, models.IntegerField):
                field.__dict__.pop("validators", None)


@pytest.fixture
def unstated_ranges(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The backend states no integer column's range, as Django 4.2's SQLite backend does.

    From 5.0 the SQLite backend states 64 bits, and Django refuses a value
    past them itself, so this is what lets the kernel's own refusal be held
    on every version the suite runs on.
    """
    # On the class: each thread has its own connection, and async writes run on another.
    monkeypatch.setattr(
        type(connection.ops), "integer_field_range", lambda self, internal_type: (None, None)
    )
    _forget_range_validators()
    yield
    _forget_range_validators()
