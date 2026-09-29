"""``UnsupportedMediaType``: a refusal of the call, never the operation's own verdict."""

from __future__ import annotations

from django_service_specs.http.unsupported_media_type import UnsupportedMediaType
from django_service_specs.services.service_error import ServiceError
from django_service_specs.types.dispatch_error import DispatchError


def test_it_is_a_dispatch_refusal_and_not_a_service_one() -> None:
    # A service error is one an agent reads and routes around; a body the
    # transport could not read is the caller's framing to fix.
    refused = UnsupportedMediaType()
    assert isinstance(refused, DispatchError)
    assert not isinstance(refused, ServiceError)


def test_its_default_message() -> None:
    assert UnsupportedMediaType().message == (
        "The request body is in a format this transport does not read."
    )
