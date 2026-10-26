"""Requests, principals and specs the HTTP transport's tests share."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponseBase
from django.test import AsyncRequestFactory, RequestFactory
from django_service_specs import (
    AdditionalInputRequired,
    Output,
    Parameter,
    Parameters,
    PermissionCheck,
    Presenter,
    SelectorKind,
    SelectorSpec,
    ServiceConflict,
    ServiceSpec,
)

from tests.dispatch.utils import OPEN, PK, Record, Titled, Titles, note_by_pk, notes_of
from tests.dispatch_app.models import Note

FACTORY = RequestFactory()
ASYNC_FACTORY = AsyncRequestFactory()

DEEP_JSON = b"[" * 1_000_000 + b"]" * 1_000_000
"""A body nested past the decoder's reach, which ``json.loads`` answers with ``RecursionError``.

The reach moves with the interpreter and with the thread. Through 3.13 it is
a counter: about 1,000 levels on 3.10 and 3.11, and 10,000 on 3.12 and 3.13.
From 3.14 it is the stack, and so the environment the suite runs in. On
macOS: about 74,000 levels on an 8 MB main thread, 150,000 on the 16 MB
thread macOS gives Python's workers, which is where the async path decodes,
and 600,000 on the 64 MB main thread a ``make`` target runs with, because
macOS make raises the stack limit to the hard one for its children. On
Linux with an 8 MB stack, about 87,000 on either thread. A body sized for
one of those decodes under another, and a test sent it is refused as
"Expected an object." without reaching the refusal it names. 1,000,000
levels is past each, at 2 MB, under the 2.5 MB body Django reads by default.
"""


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


SPEC_OR_SOURCE = pytest.mark.parametrize(
    "served", [lambda spec: spec, Foreign], ids=["spec", "source"]
)
"""A test run on a spec, and on a source compiling to it, which is served as its compiled spec."""


def signed_in(request: HttpRequest, user: Any = None) -> HttpRequest:
    """``request`` as AuthenticationMiddleware leaves it: carrying its user, anonymous or not."""
    request.user = AnonymousUser() if user is None else user
    return request


def body(response: HttpResponseBase) -> Any:
    """What a client reads: the response's own bytes, decoded."""
    assert response["Content-Type"] == "application/json"
    return json.loads(response.content)


class Seen(PermissionCheck):
    """Admits every principal, and records each, so a test can see who reached the check."""

    def __init__(self) -> None:
        self.principals: list[Any] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        self.principals.append(principal)
        return True


class Conflicting(Presenter):
    """Refuses while presenting, as a presenter that re-reads through a service may."""

    def output(self) -> Output:
        return Output(())

    def present(self, value: Any) -> Any:
        raise ServiceConflict("Moved while you were reading.")


def notes_spec(**kwargs: Any) -> SelectorSpec:
    """The principal's own notes, each as its title."""
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("presenter", Titles())
    return SelectorSpec(kind=SelectorKind.LIST, selector=notes_of, **kwargs)


def note_spec(**kwargs: Any) -> SelectorSpec:
    """One note by ``pk``, as its title; not found when there is no such row."""
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("presenter", Titles())
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK, **kwargs)


def titled_spec(returns: Any = None, **kwargs: Any) -> ServiceSpec:
    """A write taking a required ``title``, whose service returns ``returns`` unpresented."""
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("validator", Titled())
    return ServiceSpec(service=Record(returns), **kwargs)


def asking_spec(schema: Any) -> ServiceSpec:
    """A write that answers every call by asking for more, under ``schema``.

    It declares ``title`` and nothing else, so a schema keyed ``title`` asks for
    a declared value and any other key asks for one the closed argument set
    would refuse.
    """

    def ask(**pool: Any) -> None:
        raise AdditionalInputRequired("Say which.", schema=schema)

    return ServiceSpec(service=ask, permissions=OPEN, validator=Titled())


class SeesTenant(PermissionCheck):
    """Admits every principal, and records the ``tenant`` seed each call was resolved with."""

    def __init__(self) -> None:
        self.tenants: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, tenant: Any) -> bool:
        self.tenants.append(tenant)
        return True


def first_notes(*, user: Any, limit: int | None = None) -> QuerySet[Note]:
    """The principal's notes in the order written, the first ``limit`` of them if one is given."""
    notes = Note.objects.filter(owner=user).order_by("pk")
    return notes if limit is None else notes[:limit]


def limited_spec(**kwargs: Any) -> SelectorSpec:
    """The principal's notes as titles, reading an optional integer ``limit`` its selector defaults."""
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("presenter", Titles())
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=first_notes,
        reads=Parameters.of(Parameter("limit", "integer")),
        **kwargs,
    )
