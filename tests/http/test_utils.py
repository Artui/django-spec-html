"""The helpers the HTTP transport's functions and views share."""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured

from django_service_specs.authorization.principal_unavailable import PrincipalUnavailable
from django_service_specs.http.adispatch_request import adispatch_request
from django_service_specs.http.dispatch_request import dispatch_request
from django_service_specs.http.utils import (
    answered_methods,
    request_principal,
    route_arguments,
    success_response,
)
from django_service_specs.parameters.parameter import Parameter
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.specs.selector_kind import SelectorKind
from django_service_specs.specs.selector_spec import SelectorSpec
from tests.dispatch_app.models import Note
from tests.http.utils import ASYNC_FACTORY, FACTORY, body, note_spec, signed_in, titled_spec


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


@pytest.mark.parametrize("status", [200, 201, 202])
def test_a_service_presenting_nothing_answers_an_empty_body_at_the_callers_status(
    status: int,
) -> None:
    # As djangorestframework-services answers it: DRF renders ``None`` as no
    # bytes and then drops the Content-Type, which would describe nothing. The
    # status stays the caller's rather than becoming 204.
    response = success_response(titled_spec(), None, status)
    assert (response.status_code, response.content) == (status, b"")
    assert "Content-Type" not in response


@pytest.mark.parametrize(
    ("spec", "presented", "status"),
    [(titled_spec(), None, None), (titled_spec(), None, 204), (note_spec(), "Hello", 204)],
    ids=["default", "service-at-204", "read-at-204"],
)
def test_a_204_carries_no_body_and_no_content_type(spec: Any, presented: Any, status: Any) -> None:
    response = success_response(spec, presented, status)
    assert (response.status_code, response.content) == (204, b"")
    assert "Content-Type" not in response


def _nothing_left(*, result: Any) -> Any:
    return Note.objects.none()


@pytest.mark.django_db
def test_an_empty_re_read_keeps_the_callers_status() -> None:
    # djangorestframework-services answers 204 here whatever status was named;
    # the kernel keeps the caller's, since an empty re-read is the value None.
    spec = titled_spec(
        "done",
        output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_nothing_left),
    )
    response = dispatch_request(spec, _posted(FACTORY), success_status=201)
    assert (response.status_code, response.content) == (201, b"")
    assert "Content-Type" not in response


def test_a_read_whose_value_is_none_answers_null_at_any_status() -> None:
    # A read's ``None`` is its value, not the absence of one, so it is a body.
    response = success_response(note_spec(allow_none=True), None, 201)
    assert (response.status_code, body(response)) == (201, None)


def _posted(factory: Any) -> Any:
    request = factory.post("/", data=json.dumps({"title": "x"}), content_type="application/json")
    return signed_in(request)


@pytest.mark.django_db(transaction=True)
async def test_both_entry_points_answer_through_success_response() -> None:
    # Each entry point builds its success answer here and nowhere else, so
    # both answer a service presenting nothing with the same empty body.
    synced = await sync_to_async(dispatch_request)(
        titled_spec(None), _posted(FACTORY), success_status=201
    )
    awaited = await adispatch_request(titled_spec(None), _posted(ASYNC_FACTORY), success_status=201)
    for response in (synced, awaited):
        assert (response.status_code, response.content) == (201, b"")
        assert "Content-Type" not in response
