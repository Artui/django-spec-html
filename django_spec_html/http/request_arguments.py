from __future__ import annotations

import json
from collections.abc import Mapping
from io import BytesIO
from typing import Any, NoReturn

from django.http import HttpRequest, QueryDict
from django.http.multipartparser import MultiPartParser
from django.utils.datastructures import MultiValueDict
from django.utils.translation import gettext
from django_service_specs import (
    NON_FIELD_ERRORS,
    CodedMessage,
    InvalidArguments,
    Parameters,
    ServerArguments,
    coerce_flat,
    expected_type_refusal,
    flat_arguments,
)

from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
from django_spec_html.http.utils import refuse_server_owned, server_names, without

_URLENCODED = "application/x-www-form-urlencoded"
_MULTIPART = "multipart/form-data"


def request_arguments(
    request: HttpRequest,
    parameters: Parameters,
    *,
    url_kwargs: Mapping[str, Any] | None = None,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None,
    server_arguments: ServerArguments | None = None,
) -> dict[str, Any]:
    """The arguments a request carries for ``parameters``, as a JSON caller would send them."""
    route = route_arguments(parameters, url_kwargs, route_kwargs=route_kwargs)
    owned = server_names(server_arguments)
    refuse_server_owned(url_kwargs, route_kwargs, owned)
    if request.method in ("GET", "HEAD"):
        read = {**flat_arguments(parameters, request.GET), **route}
        return coerce_flat(parameters, without(read, owned))
    if request.content_type == "application/json":
        return {
            **without(_json_object(request), owned),
            **coerce_flat(parameters, route),
        }
    fields, uploads = _form(request)
    read = {**flat_arguments(parameters, fields), **_uploads(parameters, uploads), **route}
    return coerce_flat(parameters, without(read, owned))


def _uploads(parameters: Parameters, source: MultiValueDict) -> dict[str, Any]:
    """A body's uploads, by name: every one for an array, the last for anything else."""
    return {
        key: source.getlist(key)
        if (param := parameters.get(key)) is not None and param.type == "array"
        else source[key]
        for key in source
    }


def _form(request: HttpRequest) -> tuple[QueryDict, MultiValueDict]:
    """The form fields and the uploads a request's body carries, whatever its method."""
    if request.content_type not in (_URLENCODED, _MULTIPART):
        if request.body:
            raise UnsupportedMediaType(
                gettext('Unsupported media type "%(media_type)s" in request.')
                % {"media_type": request.content_type}
            )
        return QueryDict(encoding=request.encoding), MultiValueDict()
    if request.method == "POST":
        return request.POST, request.FILES
    if request.content_type == _URLENCODED:
        return QueryDict(request.body, encoding=request.encoding), MultiValueDict()
    started: bool = request._read_started  # ty: ignore[unresolved-attribute]
    stream = BytesIO(request.body) if started else request
    return MultiPartParser(request.META, stream, request.upload_handlers, request.encoding).parse()


def _json_object(request: HttpRequest) -> dict[str, Any]:
    """A JSON body as the object it must be, or the refusal a malformed one is."""
    if not request.body:
        return {}
    try:
        decoded = json.loads(request.body, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        raise InvalidArguments(
            {
                NON_FIELD_ERRORS: [
                    CodedMessage(gettext("The request body is not valid JSON."), "parse_error")
                ]
            }
        ) from None
    if not isinstance(decoded, dict):
        raise InvalidArguments({NON_FIELD_ERRORS: [expected_type_refusal("object")]})
    return decoded


def _refuse_constant(constant: str) -> NoReturn:
    """Refuse ``NaN`` and the infinities, which Python's decoder takes and JSON does not have."""
    raise ValueError(f"{constant} is not JSON.")
