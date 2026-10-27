"""``SpecAction`` - one spec a ``ModelAdmin`` offers as an admin action."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from django_service_specs import ServiceSpec


@dataclasses.dataclass(frozen=True)
class SpecAction:
    """A spec served as an admin action, and which of its target's reads the admin fills.

    A value and nothing more: ``SpecActionsMixin`` reads a ``ModelAdmin``'s
    ``spec_actions`` when the admin is registered, and refuses there what it
    cannot serve. Which kind of action it is follows from the spec's target,
    never from the declaration: a spec with an ``instance_selector_spec`` is
    an **object action**, linked from the change page of the row it acts on,
    and one with a ``collection_selector_spec`` a **changelist action**,
    offered in the action dropdown with the selected rows as its set.

    Attributes:
        name: The action's name: the last segment of its URL, the suffix of
            its URL name (``admin:<app>_<model>_<name>``), and a changelist
            action's key among the admin's actions. A slug, unique among the
            admin's ``spec_actions``.
        spec: The operation, a ``ServiceSpec`` with one target selector. A
            ``FormValidator`` makes its page ``SpecFormView``'s, no Validator
            ``SpecConfirmView``'s.
        label: What the admin calls it: the object-tools link, the dropdown
            entry and the page's title. Formatted in each as Django formats
            an action's description, with the model's ``verbose_name`` and
            ``verbose_name_plural``, so a literal ``%`` is written ``%%``,
            and a label that does not format is refused when the admin is
            registered.
        parameter: The target selector's read the admin fills: the change
            page's object id for an object action, the selected primary keys,
            an ``array``, for a changelist action.
        page_kwargs: Any other keyword the page's ``as_view()`` takes, such
            as ``display_target``, ``commit_button``, ``asked_fields`` or
            ``success_message``, checked by ``as_view()`` as on any page.
            Copied into a read-only mapping when the value is built.
    """

    name: str
    spec: ServiceSpec
    label: str
    parameter: str
    page_kwargs: Mapping[str, Any] = dataclasses.field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        # ``object.__setattr__`` because the dataclass is frozen; a copy, so a
        # key added later to the caller's dict never reaches a page already built.
        object.__setattr__(self, "page_kwargs", MappingProxyType(dict(self.page_kwargs)))
