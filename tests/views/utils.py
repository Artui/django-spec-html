"""Doubles the page tests share."""

from __future__ import annotations

from typing import Any

from django_service_specs import PermissionCheck


class SeesTenantInTheScope(PermissionCheck):
    """Admits every principal and scope, recording the ``tenant`` seed its scope check was asked with."""

    def __init__(self) -> None:
        self.tenants: list[Any] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any, *, tenant: Any) -> bool:
        self.tenants.append(tenant)
        return True
