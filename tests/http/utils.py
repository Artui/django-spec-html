"""Requests, principals and specs the HTTP transport's tests share."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from django import forms
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponseBase
from django.test import AsyncRequestFactory, RequestFactory
from django_service_specs import (
    AdditionalInputRequired,
    Affordance,
    FormValidator,
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


def asking_for_tenant_spec(schema: Any) -> ServiceSpec:
    """A write that asks under ``schema``, declaring ``title`` and a ``tenant`` read.

    ``tenant`` is declared outside the Validator, which is what makes a ``tenant``
    seed the server's: a Validator field named like a seed is left to the caller
    (``server_owned_keys``). Without that seed, ``tenant`` is an ordinary optional
    read the caller may answer, so one schema is askable on a request that
    carries no seed and refused on one that does. It is ``reflected`` because
    dispatch refuses a plain parameter a registered seed occupies, before the
    service can ask anything.
    """

    def ask(**pool: Any) -> None:
        raise AdditionalInputRequired("Say which.", schema=schema)

    return ServiceSpec(
        service=ask,
        permissions=OPEN,
        validator=Titled(),
        reads=Parameters.of(Parameter("tenant", "string", reflected=True)),
    )


class SeesTenant(PermissionCheck):
    """Admits every principal, and records the ``tenant`` seed each call was resolved with."""

    def __init__(self) -> None:
        self.tenants: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, tenant: Any) -> bool:
        self.tenants.append(tenant)
        return True


def notes_in(*, user: Any, tenant: str = "") -> QuerySet[Note]:
    """The principal's notes, whatever ``tenant`` the call carries."""
    return Note.objects.filter(owner=user)


def reflected_tenant_spec() -> SelectorSpec:
    """A read declaring ``tenant`` as ``reflected``: a seed of that name fills it on every call."""
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=notes_in,
        reads=Parameters.of(Parameter("tenant", "string", reflected=True)),
        permissions=OPEN,
        presenter=Titles(),
    )


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


class TitleForm(forms.Form):
    """One required ``title``, so a post without one is refused by the form."""

    title = forms.CharField(max_length=20)


@dataclass
class Handed:
    """The ``request`` and ``view`` each stage of one call was handed, in the order the stages ran.

    Each stage of ``handed_spec`` names both, so a stage the transport left
    them out of is handed ``None`` by the kernel, and one it never reached is
    missing from ``stages()``.
    """

    seen: list[tuple[str, Any, Any]] = field(default_factory=list)

    def record(self, stage: str, request: Any, view: Any) -> None:
        self.seen.append((stage, request, view))

    def stages(self) -> set[str]:
        return {stage for stage, _, _ in self.seen}

    def one_view(self, request: HttpRequest) -> Any:
        """The one view every stage was handed, each beside this very ``request``."""
        assert self.seen
        for stage, handed, _ in self.seen:
            assert handed is request, stage
        assert len({id(view) for _, _, view in self.seen}) == 1, self.seen
        return self.seen[0][2]


class HandedCheck(PermissionCheck):
    """Admits every principal and row, recording what each question is handed."""

    def __init__(self, handed: Handed) -> None:
        self.handed = handed

    def has_permission(self, principal: Any, spec: Any, *, request: Any, view: Any) -> bool:
        self.handed.record("class", request, view)
        return True

    def has_object_permission(
        self, principal: Any, spec: Any, obj: Any, *, request: Any, view: Any
    ) -> bool:
        self.handed.record("object", request, view)
        return True


def handed_spec(handed: Handed, **kwargs: Any) -> ServiceSpec:
    """A write on the note ``pk`` names, every stage of which names ``request`` and ``view``.

    The target's selector, both permission questions, an affordance
    condition, the ``FormValidator``'s ``form_kwargs`` and the service each
    record what they are handed in ``handed``. ``kwargs`` replace any of them.
    """

    def row(*, pk: int, request: Any, view: Any) -> QuerySet[Note]:
        handed.record("selector", request, view)
        return note_by_pk(pk=pk)

    def open_now(*, request: Any, view: Any) -> bool:
        handed.record("affordance", request, view)
        return True

    def keywords(*, request: Any, view: Any) -> dict[str, Any]:
        handed.record("form", request, view)
        return {}

    def service(*, data: Any, request: Any, view: Any) -> None:
        handed.record("service", request, view)

    kwargs.setdefault("service", service)
    kwargs.setdefault("permissions", [HandedCheck(handed)])
    kwargs.setdefault("validator", FormValidator(TitleForm, form_kwargs=keywords))
    kwargs.setdefault(
        "instance_selector_spec",
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=row, reads=PK),
    )
    kwargs.setdefault("affordances", [Affordance(code="open", reason="Open.", when=open_now)])
    return ServiceSpec(**kwargs)


HANDED_STAGES = frozenset({"selector", "class", "object", "affordance", "form", "service"})
"""Every stage of ``handed_spec`` a call reaching its service runs."""
