"""The helpers the HTTP transport's functions and views share."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured

from django_service_specs.authorization.principal_unavailable import PrincipalUnavailable
from django_service_specs.http.utils import answered_methods, request_principal, route_arguments
from django_service_specs.parameters.parameter import Parameter
from django_service_specs.parameters.parameters import Parameters
from tests.http.utils import FACTORY, note_spec, signed_in, titled_spec


class Principal:
    """A user object from a custom model with no ``is_active`` at all."""

    is_authenticated = True


class Deactivated:
    is_authenticated = True
    is_active = False


def test_a_principal_with_no_is_active_is_taken_as_active() -> None:
    # The default in ``getattr(user, "is_active", True)``: Django's own
    # ModelBackend reads a missing ``is_active`` the same way.
    principal = Principal()
    assert request_principal(signed_in(FACTORY.get("/"), principal)) is principal


def test_anonymous_is_the_principal_and_not_refused() -> None:
    assert isinstance(request_principal(signed_in(FACTORY.get("/"))), AnonymousUser)


def test_a_deactivated_principal_is_refused() -> None:
    with pytest.raises(PrincipalUnavailable):
        request_principal(signed_in(FACTORY.get("/"), Deactivated()))


@pytest.mark.parametrize(
    ("spec", "methods", "expected"),
    [
        (note_spec(), None, ("get", "head")),
        (titled_spec(), None, ("post",)),
        (titled_spec(), ("DELETE",), ("delete",)),
        (note_spec(), (), ()),
    ],
    ids=["selector", "service", "named", "none-named"],
)
def test_the_methods_a_spec_answers(spec: Any, methods: Any, expected: Any) -> None:
    assert answered_methods(spec, methods, label="SpecView") == expected


ROUTED = Parameters.of(Parameter("pk", "integer"), Parameter("slug", "string"))


def test_a_declared_route_is_returned_as_a_plain_uncoerced_copy() -> None:
    captured = {"pk": "4", "slug": "x"}
    route = route_arguments(ROUTED, captured)
    assert route == captured and route is not captured and type(route) is dict


def test_no_route_is_no_arguments() -> None:
    assert route_arguments(ROUTED, None) == {}


def test_every_undeclared_kwarg_is_named_in_order() -> None:
    with pytest.raises(ImproperlyConfigured) as refused:
        route_arguments(ROUTED, {"pk": "4", "team": "a", "org": "b"})
    assert str(refused.value).startswith(
        "The route captures org, team, which the spec does not declare."
    )
