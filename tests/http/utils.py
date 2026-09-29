"""Requests, principals and specs the HTTP transport's tests share."""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponseBase
from django.test import AsyncRequestFactory, RequestFactory

from django_service_specs.authorization.permission_check import PermissionCheck
from django_service_specs.output.output import Output
from django_service_specs.output.presenter import Presenter
from django_service_specs.services.service_conflict import ServiceConflict
from django_service_specs.specs.selector_kind import SelectorKind
from django_service_specs.specs.selector_spec import SelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec
from tests.dispatch.utils import OPEN, PK, Record, Titled, Titles, note_by_pk, notes_of

FACTORY = RequestFactory()
ASYNC_FACTORY = AsyncRequestFactory()


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
