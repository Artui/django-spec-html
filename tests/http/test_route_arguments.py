"""``route_arguments``: the kwargs a route captured, as arguments the spec declares."""

from __future__ import annotations

from typing import Any, cast

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


UNDECLARED = "The route captures"
"""How the strict rule's refusal opens, so a test of another refusal can assert it is not this one."""


class TestRouteKwargs:
    def test_none_keeps_the_strict_rule(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="The route captures org,"):
            route_arguments(ROUTED, {"pk": "4", "org": "acme"}, route_kwargs=None)

    def test_a_mapping_renames_a_captured_kwarg_to_the_parameter_it_fills(self) -> None:
        route = route_arguments(ROUTED, {"pk": "4", "org": "acme"}, route_kwargs={"org": "slug"})
        assert route == {"pk": "4", "slug": "acme"}

    def test_a_mapping_to_none_drops_a_captured_kwarg(self) -> None:
        # The ``include()`` prefix shape: ``<org>/`` reaches every view under
        # it, including one whose spec does not read it.
        route = route_arguments(ROUTED, {"org": "acme", "pk": "4"}, route_kwargs={"org": None})
        assert route == {"pk": "4"}

    def test_a_mapping_leaves_a_kwarg_it_does_not_name_to_the_strict_rule(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            route_arguments(ROUTED, {"org": "acme", "team": "a"}, route_kwargs={"org": None})
        assert str(refused.value).startswith(
            "The route captures team, which the spec does not declare."
        )

    def test_a_mapped_target_the_spec_does_not_declare_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            route_arguments(
                ROUTED, {"org": "acme"}, route_kwargs={"org": "tenant", "team": "squad"}
            )
        message = str(refused.value)
        assert message.startswith(
            "route_kwargs maps a kwarg to squad, tenant, which the spec does not declare."
        )
        assert UNDECLARED not in message

    def test_a_mapped_target_is_checked_on_a_route_that_does_not_capture_it(self) -> None:
        # The mapping is the host's, the same for every route the view is
        # mounted on, so a typo in it fails on the first request any route serves.
        with pytest.raises(ImproperlyConfigured, match="maps a kwarg to tenant,"):
            route_arguments(ROUTED, {"pk": "4"}, route_kwargs={"org": "tenant"})

    def test_two_kwargs_filling_one_parameter_are_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            route_arguments(ROUTED, {"org": "acme", "slug": "x"}, route_kwargs={"org": "slug"})
        message = str(refused.value)
        assert message.startswith("The route fills slug from more than one kwarg")
        assert UNDECLARED not in message

    def test_a_tuple_is_an_allow_list_dropping_every_other_kwarg(self) -> None:
        route = route_arguments(
            ROUTED, {"org": "acme", "pk": "4", "team": "a"}, route_kwargs=("pk",)
        )
        assert route == {"pk": "4"}

    def test_an_allow_listed_name_the_spec_does_not_declare_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            route_arguments(ROUTED, {"pk": "4"}, route_kwargs=("pk", "team", "org"))
        message = str(refused.value)
        assert message.startswith("route_kwargs allows org, team, which the spec does not declare.")
        assert UNDECLARED not in message

    def test_a_bare_string_is_refused_rather_than_read_as_its_characters(self) -> None:
        with pytest.raises(TypeError, match=r"route_kwargs is a tuple of names"):
            route_arguments(ROUTED, {"pk": "4"}, route_kwargs=cast(Any, "pk"))

    def test_the_url_kwargs_may_still_be_passed_by_position(self) -> None:
        # The page reads the route this way, with no route_kwargs to give.
        assert route_arguments(ROUTED, {"pk": "4"}) == {"pk": "4"}


def test_it_is_public_from_the_subpackage_and_the_root() -> None:
    # A host reading arguments its own way merges the route as the entry
    # points do, and refuses an undeclared kwarg as the same misconfiguration.
    assert "route_arguments" in django_spec_html.http.__all__
    assert "route_arguments" in django_spec_html.__all__
