"""``error_response`` - a refusal of either family, as the JSON an HTTP client reads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import JsonResponse

from django_service_specs.authorization.not_permitted import NotPermitted
from django_service_specs.authorization.principal_unavailable import PrincipalUnavailable
from django_service_specs.http.unsupported_media_type import UnsupportedMediaType
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.parameters.utils import NON_FIELD_ERRORS
from django_service_specs.services.action_unavailable import ActionUnavailable
from django_service_specs.services.additional_input_required import AdditionalInputRequired
from django_service_specs.services.service_conflict import ServiceConflict
from django_service_specs.services.service_error import ServiceError
from django_service_specs.services.service_not_found import ServiceNotFound
from django_service_specs.services.service_validation_error import ServiceValidationError
from django_service_specs.types.dispatch_error import DispatchError

_STATUSES: tuple[tuple[type[Exception], int], ...] = (
    (NotPermitted, 403),
    (PrincipalUnavailable, 403),
    (ServiceNotFound, 404),
    (ServiceConflict, 409),
    (ServiceError, 422),
    (UnsupportedMediaType, 415),
    (DispatchError, 400),
)
"""Each message-carrying refusal's status, read top to bottom, first match wins.

A subclass is listed above its base, or the base's row would answer for it:
``ServiceNotFound`` and ``ServiceConflict`` are ``ServiceError``s too. Coverage
sees one loop, not seven rows, so each row is held by its own case of
``test_a_message_refusal_is_its_status_and_a_detail``."""


def error_response(exc: DispatchError | ServiceError) -> JsonResponse:
    """``exc`` as JSON, at the status djangorestframework-services answers it with.

    One ladder for both families, at djangorestframework-services' statuses.
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
    if isinstance(exc, InvalidArguments):
        return JsonResponse(exc.detail, status=400)
    if isinstance(exc, ServiceValidationError):
        return JsonResponse(_field_map(exc.detail), status=400)
    # Both above the ladder, whose ServiceConflict and ServiceError rows would
    # otherwise answer them with the detail alone.
    if isinstance(exc, ActionUnavailable):
        return JsonResponse({"detail": exc.message, "code": exc.code}, status=409)
    # Two conjuncts, each held in tests/http/test_error_response.py:
    # ``test_without_a_schema_the_body_is_the_plain_detail`` fails without the
    # second (a missing schema would be written as null), and the ``Postponed``
    # row of ``test_a_message_refusal_is_its_status_and_a_detail`` without the
    # first (a plain service error has no schema to read).
    if isinstance(exc, AdditionalInputRequired) and exc.schema is not None:
        return JsonResponse({"detail": exc.message, "schema": _plain(exc.schema)}, status=422)
    status = next(status for kind, status in _STATUSES if isinstance(exc, kind))
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


def _field_map(detail: Any) -> Mapping[Any, Any]:
    """A service's detail as the field map every 400 body is."""
    if isinstance(detail, Mapping):
        return detail
    if isinstance(detail, list):
        return {NON_FIELD_ERRORS: list(detail)}
    # Anything else is one message: a string, or a lazy one. A lazy string is
    # not a ``str``, so a ladder asking "is it a string?" and reading the rest
    # as a sequence would take it apart into characters.
    return {NON_FIELD_ERRORS: [detail]}
