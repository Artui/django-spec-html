from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from django_service_specs import ServiceSpec


@dataclasses.dataclass(frozen=True)
class SpecAction:
    """A spec served as an admin action, and which of its target's reads the admin fills."""

    name: str
    spec: ServiceSpec
    label: str
    parameter: str
    page_kwargs: Mapping[str, Any] = dataclasses.field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        # ``object.__setattr__``, since the dataclass is frozen.
        object.__setattr__(self, "page_kwargs", MappingProxyType(dict(self.page_kwargs)))
