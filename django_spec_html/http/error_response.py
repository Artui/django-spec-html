"""``error_response`` - a refusal of either family, as the JSON an HTTP client reads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import JsonResponse
from django_service_specs import (
    ActionUnavailable,
    AdditionalInputRequired,
    DispatchError,
    InvalidArguments,
    ServiceError,
    ServiceValidationError,
)

from django_spec_html.http.utils import field_map, refusal_status


def error_response(exc: DispatchError | ServiceError) -> JsonResponse:
    """``exc`` as JSON, at the status djangorestframework-services answers it with.

    One map for both families, at djangorestframework-services' statuses, and
    the one the page reads too.
    Two bodies differ from its answer. A service's string or list detail, which
    DRF answers as a bare list and this as a field map under
    ``non_field_errors``, so every 400 here has one shape. And an
    ``AdditionalInputRequired`` schema, which DRF reads as an error detail and
    so writes with every value a string (``"minimum": "1"``), and this writes as
    the JSON Schema it is:

    | Refusal | Status | Body |
    | --- | --- | --- |
    | `InvalidArguments` | 400 | the error tree |
    | `ServiceValidationError` | 400 | its detail, as a field map |
    | `NotPermitted`, `PrincipalUnavailable` | 403 | `{"detail": message}` |
    | `ServiceNotFound` | 404 | `{"detail": message}` |
    | `ActionUnavailable` | 409 | `{"detail": message, "code": code}` |
    | any other `ServiceConflict` | 409 | `{"detail": message}` |
    | `AdditionalInputRequired` with a `schema` | 422 | `{"detail": message, "schema": schema}` |
    | any other `ServiceError` | 422 | `{"detail": message}` |
    | `UnsupportedMediaType` | 415 | `{"detail": message}` |
    | any other `DispatchError` | 400 | `{"detail": message}` |

    **Every 400 body is a field map.** ``InvalidArguments`` is its tree, whose
    integer row keys JSON writes as strings. A ``ServiceValidationError``'s
    mapping is its body as it is, and a string or a list goes under
    ``non_field_errors``, where a message about the input as a whole sits in
    the tree too. A lazy translation is one message, not a sequence of
    characters, and renders in the active language: ``JsonResponse``'s
    encoder renders every lazy string it meets, messages included.

    **Never 401.** A session-authenticated view has no challenge to answer,
    and a 401 tells a client to start an authentication flow it has no way to
    finish. An anonymous caller a permission check refuses is a 403, like any
    other refused principal.

    **Two members add a key beside ``detail``, and keep their base's status.**
    ``ActionUnavailable`` is a conflict, and its arm exists only to put the
    affordance's ``code`` in the body beside the ``detail`` a client already
    reads: the stable code is the whole point of the member, and a client left
    with only the sentence has nothing to branch on. ``AdditionalInputRequired``
    is "I need one more value", which is the operation being unprocessable as
    asked, so it stays a 422 like any other service error; the ``schema`` naming
    what is missing joins the body, because without it a client is told that
    something is needed and not what. Only when there is a schema: the error is
    valid without one, and growing the body unconditionally would change every
    plain message into an object for no gain.

    A ``DispatchError`` no row names - raised by nobody in this package, or a
    subclass a later release adds - is still a refusal of the call rather than
    the operation's own verdict, so it answers 400 rather than 422.
    """
    status = refusal_status(exc)
    if isinstance(exc, InvalidArguments):
        return JsonResponse(exc.detail, status=status)
    if isinstance(exc, ServiceValidationError):
        return JsonResponse(field_map(exc.detail), status=status)
    if isinstance(exc, ActionUnavailable):
        return JsonResponse({"detail": exc.message, "code": exc.code}, status=status)
    # Two conjuncts, each held in tests/http/test_error_response.py:
    # ``test_without_a_schema_the_body_is_the_plain_detail`` fails without the
    # second (a missing schema would be written as null), and the ``Postponed``
    # row of ``test_a_message_refusal_is_its_status_and_a_detail`` without the
    # first (a plain service error has no schema to read).
    if isinstance(exc, AdditionalInputRequired) and exc.schema is not None:
        return JsonResponse({"detail": exc.message, "schema": _plain(exc.schema)}, status=status)
    return JsonResponse({"detail": exc.message}, status=status)


def _plain(value: Any) -> Any:
    """``value`` with every mapping in it a ``dict``, at any depth.

    The encoder writes only a ``dict`` as an object, and a schema may arrive
    as any mapping - a frozen one, say - at the top or nested in a property,
    where it would otherwise be a 500. Both container kinds are held by
    ``test_a_schema_of_any_mapping_is_written_at_any_depth``.
    """
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
