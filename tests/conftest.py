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
    """No integer column states a range, so the kernel's own refusal is held on every Django."""
    monkeypatch.setattr(
        type(connection.ops), "integer_field_range", lambda self, internal_type: (None, None)
    )
    _forget_range_validators()
    yield
    _forget_range_validators()
