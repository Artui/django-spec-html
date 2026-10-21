"""``route_arguments``: the kwargs a route captured, as arguments the spec declares."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import InvalidArguments, Parameter, Parameters, coerce_flat

import django_spec_html
import django_spec_html.http
from django_spec_html.http.route_arguments import route_arguments

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


def test_merged_last_and_typed_with_the_rest_one_refusal_carries_every_problem() -> None:
    # The recipe the docstring and the HTTP page give a host's own view. The
    # route comes back uncoerced, so a malformed route value is refused in the
    # same call as a malformed value the view read, rather than on its own.
    parameters = Parameters.of(Parameter("pk", "integer"), Parameter("count", "integer"))
    read = {"count": "many"}
    with pytest.raises(InvalidArguments) as refused:
        coerce_flat(parameters, {**read, **route_arguments(parameters, {"pk": "four"})})
    assert set(refused.value.detail) == {"pk", "count"}


def test_it_is_public_from_the_subpackage_and_the_root() -> None:
    # A host reading arguments its own way merges the route as the entry
    # points do, and refuses an undeclared kwarg as the same misconfiguration.
    assert "route_arguments" in django_spec_html.http.__all__
    assert "route_arguments" in django_spec_html.__all__
