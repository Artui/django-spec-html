"""Concept doubles, callables and expected messages the dispatch tests share."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import QuerySet

from django_service_specs.authorization.permission_check import PermissionCheck
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.output.output import Output
from django_service_specs.output.output_field import OutputField
from django_service_specs.output.presenter import Presenter
from django_service_specs.parameters.parameter import Parameter
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS
from django_service_specs.validation.validation_context import ValidationContext
from django_service_specs.validation.validator import Validator
from tests.dispatch_app.models import Note

OPEN = (Unrestricted(),)
PK = Parameters.of(Parameter("pk", "integer", required=True))

# A registered seed whose value names the principal it was resolved for, so a
# test can tell it from anything a caller could have sent under the same name.
TENANT_SEEDS = DEFAULT_POOL_SEEDS.extend(tenant=lambda *, user: f"tenant-of-{user.username}")


def make_user(username: str) -> Any:
    return get_user_model().objects.create_user(username=username)


def notes_of(*, user: Any) -> QuerySet[Note]:
    return Note.objects.filter(owner=user)


def note_by_pk(*, pk: int) -> QuerySet[Note]:
    # Unscoped on purpose: the object-level check is what refuses another
    # owner's row, so the selector must be able to find one.
    return Note.objects.filter(pk=pk)


class Refuse(PermissionCheck):
    """Refuses every principal at class level, in words no other check uses."""

    message = "Only editors may run this."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


class OwnerOnly(PermissionCheck):
    """Admits every principal, then refuses a row it does not own; records each row it saw."""

    message = "Only the owner may touch this note."

    def __init__(self) -> None:
        self.rows: list[Any] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_object_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        self.rows.append(target)
        return bool(target.owner_id == principal.pk)


class Titled(Validator):
    """Declares a required title and strips it; records every call.

    ``returns`` replaces the verdict, for a test about what a Validator hands
    back rather than about what it checks.
    """

    def __init__(self, returns: Mapping[str, Any] | None = None) -> None:
        self.returns = returns
        self.calls: list[tuple[dict[str, Any], ValidationContext]] = []

    def parameters(self) -> Parameters:
        return Parameters.of(Parameter("title", "string", required=True))

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        self.calls.append((dict(arguments), context))
        if self.returns is not None:
            return dict(self.returns)
        return {"title": arguments["title"].strip()}


class Titles(Presenter):
    """Renders a note as its title; records every value it was handed."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def output(self) -> Output:
        return Output((OutputField("title", "string"),))

    def present(self, value: Any) -> Any:
        self.seen.append(value)
        return {"title": value.title}


class Record:
    """A spec callable that records the keywords it received and returns ``returns``.

    Declares ``**pool``, so it receives the whole pool it was resolved from:
    what a test reads back is exactly what dispatch put there.
    """

    def __init__(self, returns: Any = None) -> None:
        self.returns = returns
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **pool: Any) -> Any:
        self.calls.append(pool)
        return self.returns


def shaping_refused(source: str, returned: str) -> str:
    """``shape_queryset``'s refusal, as it names the selector that returned a non-queryset."""
    return (
        "select_related / prefetch_related / annotations / extend_queryset are set on the "
        f"spec but {source} returned {returned}, which is not a Django QuerySet. Drop the "
        "shaping fields or have the callable return a QuerySet."
    )
