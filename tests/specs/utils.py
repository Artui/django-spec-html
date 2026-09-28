"""Minimal concept implementations the spec tests declare against."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs.authorization.permission_check import PermissionCheck
from django_service_specs.output.output import Output
from django_service_specs.output.output_field import OutputField
from django_service_specs.output.presenter import Presenter
from django_service_specs.parameters.parameter import Parameter
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.validation.validation_context import ValidationContext
from django_service_specs.validation.validator import Validator


class Titled(Validator):
    def __init__(self, *names: str) -> None:
        self.names = names or ("title",)

    def parameters(self) -> Parameters:
        return Parameters(Parameter(name, "string") for name in self.names)

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)


class Named(Presenter):
    def output(self) -> Output:
        return Output((OutputField("name", "string"),))

    def present(self, value: Any) -> Any:
        return {"name": str(value)}


class Nobody(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


PK = Parameters.of(Parameter("pk", "integer", required=True))
