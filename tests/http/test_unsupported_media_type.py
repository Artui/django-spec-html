"""``UnsupportedMediaType``: this package's refusal of the call, answered 415.

The refusal leaves the kernel with the JSON half, so no map in the kernel names
it and this package's own ``error_response`` is what answers it. The tests
here raise this package's class through each JSON entry point, since a row
that missed it would answer it with the ``DispatchError`` row's 400.
"""

from __future__ import annotations

from django_service_specs import DispatchError, ServiceError

from django_spec_html.http.adispatch_request import adispatch_request
from django_spec_html.http.dispatch_request import dispatch_request
from django_spec_html.http.error_response import error_response
from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
from tests.http.utils import ASYNC_FACTORY, FACTORY, body, signed_in, titled_spec

UNREADABLE = "application/merge-patch+json"
"""A label the reader reads neither as JSON nor as a form."""

REFUSED = {"detail": f'Unsupported media type "{UNREADABLE}" in request.'}


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


def test_error_response_answers_this_package_s_refusal_415_with_its_detail() -> None:
    # Below its own row the refusal is any other ``DispatchError``, a 400.
    response = error_response(UnsupportedMediaType("Not JSON."))
    assert response.status_code == 415
    assert body(response) == {"detail": "Not JSON."}


def test_a_body_the_reader_cannot_read_is_415_from_dispatch_request() -> None:
    request = FACTORY.post("/", data='{"title": "x"}', content_type=UNREADABLE)
    response = dispatch_request(titled_spec(), signed_in(request))
    assert response.status_code == 415
    assert body(response) == REFUSED


async def test_a_body_the_reader_cannot_read_is_415_from_adispatch_request() -> None:
    request = ASYNC_FACTORY.post("/", data='{"title": "x"}', content_type=UNREADABLE)
    response = await adispatch_request(titled_spec(), signed_in(request))
    assert response.status_code == 415
    assert body(response) == REFUSED
