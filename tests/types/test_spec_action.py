"""``SpecAction``: the declaration of a spec served as an admin action."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_html.types.spec_action import SpecAction

SPEC = ServiceSpec(service=lambda: None, permissions=[Unrestricted()])


def test_page_kwargs_is_a_read_only_copy() -> None:
    # A key added later to the caller's dict never reaches a page built from
    # the declaration, and the declaration's own refuses one.
    given: dict[str, Any] = {"display_target": True}
    action = SpecAction("archive", SPEC, "Archive", "pk", given)
    given["commit_button"] = "go"
    assert dict(action.page_kwargs) == {"display_target": True}
    with pytest.raises(TypeError):
        action.page_kwargs["commit_button"] = "go"


def test_page_kwargs_defaults_to_empty() -> None:
    assert dict(SpecAction("archive", SPEC, "Archive", "pk").page_kwargs) == {}


def test_it_is_a_frozen_value() -> None:
    action = SpecAction("archive", SPEC, "Archive", "pk")
    with pytest.raises(dataclasses.FrozenInstanceError):
        action.name = "other"
    assert action == SpecAction("archive", SPEC, "Archive", "pk")
