from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
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
    """A stand-in for djangorestframework-services' spec, compiling to a kernel one."""

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


def asking_spec(schema: Any, validator: Any = None) -> ServiceSpec:
    """A write that answers every call by asking for more, under ``schema``."""

    def ask(**pool: Any) -> None:
        raise AdditionalInputRequired("Say which.", schema=schema)

    return ServiceSpec(
        service=ask, permissions=OPEN, validator=Titled() if validator is None else validator
    )


def _owner_from_pk(*, pk: Any) -> dict[str, Any]:
    return {"owner_name": str(pk)}


def provider_read_spec() -> ServiceSpec:
    """A write whose kwargs provider requires pk itself, beside the owner_name it fills."""
    return ServiceSpec(
        service=Record(),
        permissions=OPEN,
        reads=Parameters.of(
            Parameter("owner_name", "string", required=True),
            Parameter("pk", "integer", required=True),
        ),
        kwargs=_owner_from_pk,
    )


def asking_for_tenant_spec(schema: Any) -> ServiceSpec:
    """A write that asks under ``schema``, declaring ``title`` and a ``tenant`` read."""

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
    """The principal's notes as titles, reading an optional ``limit`` its selector defaults."""
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


class CountForm(forms.Form):
    """A title and a count the form takes as text, so it cleans a string or a number alike."""

    title = forms.CharField(max_length=20)
    count = forms.CharField(required=False)


class DeclaredByTheRequest(FormValidator):
    """A Validator declaring one thing to a call with a request, another to every other reader."""

    def __init__(self, form_class: type[forms.BaseForm] = CountForm, routed: str = "") -> None:
        super().__init__(form_class)
        self.routed = routed
        self.contexts: list[dict[str, Any]] = []

    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        declared = super().parameters()
        if call_context is None or call_context.get("request") is None:
            return Parameters(
                tuple(
                    replace(each, type="integer") if each.name == "count" else each
                    for each in declared
                )
            )
        self.contexts.append(call_context)
        return (
            declared + Parameters.of(Parameter(self.routed, "string")) if self.routed else declared
        )

    def one_context(self) -> dict[str, Any]:
        """The one dict every ask of a call's declaration was handed."""
        assert self.contexts
        assert len({id(context) for context in self.contexts}) == 1, self.contexts
        assert self.contexts[0]["transport"] == "html"
        return self.contexts[0]


@dataclass
class Handed:
    """The request and view each stage of one call was handed, in the order the stages ran."""

    seen: list[tuple[str, Any, Any]] = field(default_factory=list)
    transports: list[tuple[str, str | None]] = field(default_factory=list)
    standard: list[tuple[str, Any]] = field(default_factory=list)

    def record(self, stage: str, request: Any, view: Any, call_context: Any = None) -> None:
        self.seen.append((stage, request, view))
        # The literal key, as the kernel's call_transport reads it: a rename is caught here.
        self.transports.append((stage, (call_context or {}).get("transport")))
        self.standard.append(
            (
                stage,
                {each: (call_context or {}).get(each, "missing") for each in ("format", "action")},
            )
        )

    def reset(self) -> None:
        """Forget what was recorded, between a test's two requests."""
        self.seen.clear()
        self.transports.clear()
        self.standard.clear()

    def one_transport(self) -> str | None:
        """The one transport every stage read from its context, each stage having read one."""
        assert self.seen
        assert [stage for stage, _ in self.transports] == [stage for stage, _, _ in self.seen]
        assert len({transport for _, transport in self.transports}) == 1, self.transports
        assert all(entries == {"format": None, "action": None} for _, entries in self.standard), (
            self.standard
        )
        return self.transports[0][1]

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

    def has_permission(
        self, principal: Any, spec: Any, *, request: Any, view: Any, call_context: Any
    ) -> bool:
        self.handed.record("class", request, view, call_context)
        return True

    def has_object_permission(
        self, principal: Any, spec: Any, obj: Any, *, request: Any, view: Any, call_context: Any
    ) -> bool:
        self.handed.record("object", request, view, call_context)
        return True


def handed_spec(handed: Handed, **kwargs: Any) -> ServiceSpec:
    """A write on the note ``pk`` names, every stage of which names ``request`` and ``view``."""

    def row(*, pk: int, request: Any, view: Any, call_context: Any) -> QuerySet[Note]:
        handed.record("selector", request, view, call_context)
        return note_by_pk(pk=pk)

    def open_now(*, request: Any, view: Any, call_context: Any) -> bool:
        handed.record("affordance", request, view, call_context)
        return True

    def keywords(*, request: Any, view: Any, call_context: Any) -> dict[str, Any]:
        handed.record("form", request, view, call_context)
        return {}

    def service(*, data: Any, request: Any, view: Any, call_context: Any) -> None:
        handed.record("service", request, view, call_context)

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
