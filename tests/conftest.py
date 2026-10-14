"""Fixtures shared by the suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.apps import apps
from django.db import connection, models


def _forget_range_validators() -> None:
    # A model field reads the range into its validators once and caches them,
    # so a range stated or not leaks into whichever test reads them next.
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
    # On the class, since each thread holds a connection of its own, and an
    # async write's queries run on another thread's.
    monkeypatch.setattr(
        type(connection.ops), "integer_field_range", lambda self, internal_type: (None, None)
    )
    _forget_range_validators()
    yield
    _forget_range_validators()
