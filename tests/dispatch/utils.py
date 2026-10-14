"""Concept doubles, callables and expected messages the dispatch tests share."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

from django_service_specs.authorization.permission_check import PermissionCheck
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.output.output import Output, OutputField
from django_service_specs.output.presenter import Presenter
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.types.affordance import Affordance
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


class Scoped(PermissionCheck):
    """Admits every principal; records each row and each scope it is asked about.

    ``refuse_scope`` refuses every scope, a set or nothing, in words no other
    check uses; a row is refused unless the principal owns it, as ``OwnerOnly``
    refuses one.
    """

    message = "Not within your scope."

    def __init__(self, *, refuse_scope: bool = False) -> None:
        self.refuse_scope = refuse_scope
        self.rows: list[Any] = []
        self.scopes: list[Any] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_object_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        self.rows.append(target)
        return bool(target.owner_id == principal.pk)

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any) -> bool:
        # A set is recorded as its rows, so a test reads what was asked
        # rather than a queryset that would re-query when compared.
        self.scopes.append(None if scope is None else list(scope))
        return not self.refuse_scope


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
        "select_related / prefetch_related / annotations / extend_queryset / filter_set are "
        f"set on the spec but {source} returned {returned}, which is not a Django QuerySet. "
        "Drop the shaping fields or have the callable return a QuerySet."
    )


class NoteRows(Presenter):
    """Renders a note, or a mapping shaped like one, as its pk and title."""

    def output(self) -> Output:
        return Output((OutputField("id", "integer"), OutputField("title", "string")))

    def present(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {"id": value["id"], "title": value["title"]}
        return {"id": value.pk, "title": value.title}


# Renaming a note, with one condition of each kind, so every path that answers
# a selector spec's ``affordances`` is asked both: a condition on the row, and
# a callable over the seeds. ``EDIT`` declares none, so its answer is always
# available.
NOT_ARCHIVED = Affordance(
    code="note_archived", reason="An archived note cannot be renamed.", when=Q(archived=False)
)
BOOKS_OPEN = Affordance(
    code="books_closed", reason="Renaming is paused while the books close.", when=lambda: True
)
BOOKS_CLOSED = Affordance(
    code="books_closed", reason="Renaming is paused while the books close.", when=lambda: False
)
RENAME = ServiceSpec(service=Record(), permissions=OPEN, affordances=[NOT_ARCHIVED, BOOKS_OPEN])
EDIT = ServiceSpec(service=Record(), permissions=OPEN)
REFUSED_ARCHIVED = {
    "available": False,
    "code": "note_archived",
    "reason": "An archived note cannot be renamed.",
}
REFUSED_CLOSED = {
    "available": False,
    "code": "books_closed",
    "reason": "Renaming is paused while the books close.",
}
