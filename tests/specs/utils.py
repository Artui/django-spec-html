"""Minimal concept implementations the spec tests declare against."""

from __future__ import annotations

from dataclasses import dataclass, field

from django_service_specs import (
    SelectorSpec,
    ServiceSpec,
)


@dataclass(frozen=True)
class Foreign:
    """A stand-in for djangorestframework-services' spec: a declaration compiling to a kernel one.

    Shaped as the real one is where the compile seam reads it: a frozen
    dataclass, so it compares by its fields, holding a list, so it has no hash.
    It counts its compiles, so a test can tell a cached spec from a recompiled
    one.
    """

    compiled: ServiceSpec | SelectorSpec
    labels: list[str] = field(default_factory=list)
    compiles: list[int] = field(default_factory=list, compare=False)

    def as_kernel_spec(self) -> ServiceSpec | SelectorSpec:
        self.compiles.append(1)
        return self.compiled
