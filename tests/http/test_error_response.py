"""``error_response``: each refusal as JSON, at the status djangorestframework-services answers.

Bodies are read back with ``json.loads`` from the response's own bytes, so what
is asserted is what a client receives, integer row keys and rendered lazy
translations included.
"""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any, ClassVar

import pytest
from django.http import JsonResponse
from django.utils import translation
from django.utils.translation import gettext_lazy
from django_service_specs import (
    ActionUnavailable,
    AdditionalInputRequired,
    DispatchError,
    InvalidArguments,
    NotPermitted,
    Parameter,
    Parameters,
    PrincipalUnavailable,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceValidationError,
    check_arguments,
)

from django_spec_html.http.error_response import error_response
from django_spec_html.http.unsupported_media_type import UnsupportedMediaType


class Postponed(ServiceError):
    """A service's own subclass, which the status map has never heard of."""


class Throttled(DispatchError):
    """A dispatch refusal the status map has never heard of, as a later release might add."""

    default_message: ClassVar[str] = "Slow down."


def answered(exc: DispatchError | ServiceError) -> tuple[int, Any]:
    response = error_response(exc)
    assert isinstance(response, JsonResponse)
    assert response["Content-Type"] == "application/json"
    return response.status_code, json.loads(response.content)


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (NotPermitted("Only editors."), 403),
        (PrincipalUnavailable("Gone."), 403),
        (ServiceNotFound("No such note."), 404),
        (ServiceConflict("Taken."), 409),
        (ServiceError("Not today."), 422),
        (Postponed("Later."), 422),
        (UnsupportedMediaType("Not JSON."), 415),
        (Throttled("Slow down."), 400),
        (DispatchError("Refused."), 400),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_a_message_refusal_is_its_status_and_a_detail(exc: Any, status: int) -> None:
    # Each row holds one entry of the status map: ServiceNotFound and
    # ServiceConflict are ServiceErrors too, so answering either 422 means the
    # base matched before the subclass; and an unknown DispatchError is 400
    # because it is still a refusal of the call, never a service's verdict.
    assert answered(exc) == (status, {"detail": exc.message})


def test_invalid_arguments_is_400_with_the_tree_as_the_body() -> None:
    # Row keys are ints in the tree and strings on the wire, as the tree's
    # own docstring says json.dumps writes them.
    tree = {"title": ["Required."], "books": {1: {"non_field_errors": ["Expected an object."]}}}
    assert answered(InvalidArguments(tree)) == (
        400,
        {"title": ["Required."], "books": {"1": {"non_field_errors": ["Expected an object."]}}},
    )


class TestActionUnavailable:
    """A conflict like any other, with the affordance's code beside the sentence."""

    def test_it_is_409_with_the_code_beside_the_detail(self) -> None:
        exc = ActionUnavailable("A shipped order cannot be cancelled.", code="order_shipped")
        assert answered(exc) == (
            409,
            {"detail": "A shipped order cannot be cancelled.", "code": "order_shipped"},
        )

    def test_it_is_answered_before_its_base(self) -> None:
        # Its base answers 409 too, so only the body tells the two arms apart.
        assert "code" in answered(ActionUnavailable(code="order_shipped"))[1]


class TestAdditionalInputRequired:
    """A 422 like any other service error, with what is missing beside the sentence."""

    # The guard is ``isinstance(exc, AdditionalInputRequired) and exc.schema
    # is not None``. The first test holds the pair; the second holds the
    # second half (without it, a missing schema would be written as null);
    # the parametrized ``Postponed`` row above holds the first half, since a
    # plain service error has no ``schema`` to read.
    def test_a_schema_of_any_mapping_is_written_at_any_depth(self) -> None:
        # A frozen mapping at the top, in a property, in a list and in a tuple:
        # each is an object on the wire, where the encoder alone would refuse it.
        frozen = MappingProxyType
        schema = frozen(
            {
                "choice": frozen({"oneOf": [frozen({"const": 1})], "examples": (frozen({}),)}),
            }
        )
        exc = AdditionalInputRequired("Pick one.", schema=schema)
        assert answered(exc) == (
            422,
            {
                "detail": "Pick one.",
                "schema": {"choice": {"oneOf": [{"const": 1}], "examples": [{}]}},
            },
        )

    def test_a_schema_joins_the_detail(self) -> None:
        exc = AdditionalInputRequired(
            "120 rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}, "count": {"type": "integer", "minimum": 1}},
        )
        # ``minimum`` stays a number: DRF would write it as the string "1".
        assert answered(exc) == (
            422,
            {
                "detail": "120 rows match. Confirm to proceed.",
                "schema": {
                    "confirmed": {"type": "boolean"},
                    "count": {"type": "integer", "minimum": 1},
                },
            },
        )

    def test_without_a_schema_the_body_is_the_plain_detail(self) -> None:
        assert answered(AdditionalInputRequired("Confirm to proceed.")) == (
            422,
            {"detail": "Confirm to proceed."},
        )


class TestServiceValidationError:
    """Always 400, and always a field map, whatever the service raised it with."""

    def test_a_mapping_is_the_body(self) -> None:
        assert answered(ServiceValidationError({"title": ["Too dull."]})) == (
            400,
            {"title": ["Too dull."]},
        )

    def test_a_string_sits_under_non_field_errors(self) -> None:
        assert answered(ServiceValidationError("Pick a later day.")) == (
            400,
            {"non_field_errors": ["Pick a later day."]},
        )

    def test_a_list_sits_under_non_field_errors(self) -> None:
        assert answered(ServiceValidationError(["One.", "Two."])) == (
            400,
            {"non_field_errors": ["One.", "Two."]},
        )

    def test_a_lazy_string_is_one_message_not_its_characters(self) -> None:
        # A lazy translation is not a ``str``, so a reading that asks "is it a
        # string?" before "is it a list?" iterates it into characters.
        detail: Any = gettext_lazy("This field is required.")
        with translation.override("de"):
            assert answered(ServiceValidationError(detail)) == (
                400,
                {"non_field_errors": ["Dieses Feld ist zwingend erforderlich."]},
            )

    def test_it_is_answered_before_its_base(self) -> None:
        # Its base is 422; a map matching ServiceError first would answer
        # this one 422 with a ``detail`` and lose the field map.
        status, body = answered(ServiceValidationError({"title": ["Too dull."]}))
        assert status == 400
        assert "detail" not in body


def test_a_lazy_message_renders_in_the_active_language() -> None:
    message: Any = gettext_lazy("Enter a whole number.")
    with translation.override("de"):
        assert answered(NotPermitted(message)) == (
            403,
            {"detail": "Bitte eine ganze Zahl eingeben."},
        )


def test_no_refusal_is_ever_a_401() -> None:
    # A 401 tells a client to start an authentication flow, and a session has
    # no challenge to answer: an anonymous caller a check refuses is a 403.
    refusals: list[DispatchError | ServiceError] = [
        NotPermitted(),
        PrincipalUnavailable(),
        InvalidArguments({}),
        DispatchError(),
        ServiceError(),
        ServiceValidationError("x"),
        ServiceNotFound(),
        ServiceConflict(),
        ActionUnavailable(code="c"),
        AdditionalInputRequired("x", schema={}),
        UnsupportedMediaType(),
    ]
    assert 401 not in {error_response(exc).status_code for exc in refusals}


def test_a_refusal_s_codes_never_reach_the_body() -> None:
    # The codes are for an adapter in the same process; the body a client reads
    # is the plain messages, byte for byte what it was before they existed.
    params = Parameters.of(Parameter("title", "string", required=True))
    with pytest.raises(InvalidArguments) as refused:
        check_arguments(params, {"colour": "red"})
    plain = {"title": ["This field is required."], "colour": ["Unknown argument."]}
    assert error_response(refused.value).content == json.dumps(plain).encode()
