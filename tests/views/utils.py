from __future__ import annotations

from typing import Any

from django_service_specs import PermissionCheck


class SeesTenantAtEachQuestion(PermissionCheck):
    """Admits every principal, row and scope, recording the ``tenant`` each question carried."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, Any]] = []

    def has_permission(self, principal: Any, spec: Any, *, tenant: Any) -> bool:
        self.asked.append(("class", tenant))
        return True

    def has_object_permission(self, principal: Any, spec: Any, obj: Any, *, tenant: Any) -> bool:
        self.asked.append(("object", tenant))
        return True

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any, *, tenant: Any) -> bool:
        self.asked.append(("scope", tenant))
        return True


class Fresh:
    """A seed resolver answering a new object on every call, each kept in ``resolved``."""

    def __init__(self) -> None:
        self.resolved: list[object] = []

    def __call__(self) -> object:
        self.resolved.append(object())
        return self.resolved[-1]
