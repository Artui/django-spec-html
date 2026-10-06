"""``request_arguments`` - a Django request, read as the arguments a JSON caller would send."""

from __future__ import annotations

import json
from collections.abc import Mapping
from io import BytesIO
from typing import Any, NoReturn

from django.http import HttpRequest, QueryDict
from django.http.multipartparser import MultiPartParser
from django.utils.translation import gettext

from django_service_specs.http.unsupported_media_type import UnsupportedMediaType
from django_service_specs.http.utils import route_arguments
from django_service_specs.parameters.coerce_flat import coerce_flat
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.parameters.parameter import Parameter
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.parameters.utils import NON_FIELD_ERRORS, expected_type

_CSRF_FIELD = "csrfmiddlewaretoken"
"""The field ``{% csrf_token %}`` renders into a form: Django's, and never an argument."""

_URLENCODED = "application/x-www-form-urlencoded"
_MULTIPART = "multipart/form-data"


def request_arguments(
    request: HttpRequest, parameters: Parameters, *, url_kwargs: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The arguments a request carries for ``parameters``, typed as a JSON caller would send them.

    Where they are read from depends on the method and the body:

    - **GET and HEAD**: the query string, which is flat. Any body is ignored.
    - **Any other method, with an** ``application/json`` **body**: the body,
      read as it is. It must be a JSON object; an empty body is no arguments,
      as DRF's parser reads one. Its values are not coerced: a JSON caller
      that sends ``"5"`` for an integer has sent a string, and the shape check
      says so.
    - **Any other method, with a form body** (``application/x-www-form-urlencoded``
      or ``multipart/form-data``): the fields, which are flat. Django parses
      one into ``request.POST`` for POST alone, so a PUT, PATCH or DELETE
      body is parsed here the same way rather than arriving empty. Any other
      body is refused as
      [`UnsupportedMediaType`][django_service_specs.http.unsupported_media_type.UnsupportedMediaType]
      rather than read as none, since an operation whose parameters are all
      optional would then run with nothing; a request with no body carries no
      arguments, whatever its ``Content-Type`` says.

    **A flat source** is read by the declaration. A parameter of type
    ``array`` takes every value its key was sent with, and anything else takes
    the last, as ``QueryDict`` reads a repeated key. **A blank value is
    absent unless** ``""`` **is one the parameter can take**: a plain string
    can, and a number, a boolean, a date, a decimal, or a string whose
    choices leave the blank out cannot. A flat wire has no other spelling of
    "left blank", and an empty ``?count=`` or ``?since=`` from a filter form is
    not a refusal to count or a malformed date. Each blank element of an array
    is read by the same rule, the whole array absent once nothing is left. Django's
    ``csrfmiddlewaretoken`` is dropped. What remains goes through
    [`coerce_flat`][django_service_specs.parameters.coerce_flat.coerce_flat],
    which leaves an undeclared key as it is, so the closed argument set in
    dispatch stays the one place that refuses it, under the caller's policy.

    **Files are not arguments.** Parameters have no file type, so an upload in
    a multipart body is never one. A POST's uploads are in ``request.FILES``,
    which Django fills for POST alone: those in a PUT, PATCH or DELETE body
    parsed here are dropped.

    **URL kwargs are merged last**, through ``coerce_flat`` as well since a
    path segment with no converter is a string. The route wins a clash, as it
    does in djangorestframework-services: a client-supplied value must not
    move the route's scope, so ``/notes/4/`` with ``pk=9`` in its body is about
    note 4. On a flat request the two are coerced together, so every refusal
    comes back at once and a clashing client value the route replaced is
    never refused.

    Raises:
        InvalidArguments: a JSON body that does not parse or is not an object,
            under ``non_field_errors``; or ``coerce_flat``'s refusals, each at
            its parameter's name.
        UnsupportedMediaType: a body that is neither JSON nor a form.
        ImproperlyConfigured: a URL kwarg ``parameters`` does not declare,
            which is the host's route rather than the client's request; see
            ``route_arguments`` in this subpackage's ``utils``.
    """
    route = route_arguments(parameters, url_kwargs)
    if request.method in ("GET", "HEAD"):
        return coerce_flat(parameters, {**_flat(parameters, request.GET), **route})
    if request.content_type == "application/json":
        return {**_json_object(request), **coerce_flat(parameters, route)}
    return coerce_flat(parameters, {**_flat(parameters, _form(request)), **route})


def _flat(parameters: Parameters, source: QueryDict) -> dict[str, Any]:
    """A query string or form as strings and lists of strings, blanks read as absent."""
    raw: dict[str, Any] = {}
    for key in source:
        if key == _CSRF_FIELD:
            continue
        param = parameters.get(key)
        # One branch to coverage, so each condition is held by its own test:
        # test_an_undeclared_key_passes_through_for_the_closed_set_to_refuse
        # (the first) and test_a_scalar_takes_the_last_of_repeated_values (the
        # second, without which every declared value would become a list).
        if param is not None and param.type == "array":
            keep = _takes_blank(param.items, param)
            values = [value for value in source.getlist(key) if keep or value != ""]
            if values:
                raw[key] = values
            continue
        value = source[key]
        # One branch to coverage, so each condition is held by its own test:
        # test_a_blank_string_is_kept (whether it takes a blank),
        # test_a_scalar_takes_the_last_of_repeated_values (the blank: without
        # it every integer is dropped), and
        # test_an_undeclared_key_passes_through_for_the_closed_set_to_refuse
        # (the declaration: an undeclared blank has no type to ask about).
        if (
            value == ""
            and param is not None
            and not _takes_blank(param.type, param, fmt=param.format)
        ):
            continue
        raw[key] = value
    return raw


def _takes_blank(json_type: object, param: Parameter, *, fmt: str | None = None) -> bool:
    """Whether ``""`` is a value of ``json_type`` under ``param``, so a blank is kept rather than absent.

    ``json_type`` is the parameter's own type, or an array's element type:
    ``None`` for an undeclared element, which ``coerce_flat`` leaves as the
    string it arrived as, and a ``Parameters`` for a row, which is no string.
    A format names what a string decodes into, and no date or decimal is
    blank. ``choices`` constrain a scalar and each element of an array alike,
    so a blank they leave out is refused by the shape check rather than read
    as a choice, and here it is absent instead.

    One branch to coverage, so each condition is held by its own test:
    test_blank_elements_of_an_undeclared_element_type_are_kept (``None``),
    test_blank_elements_of_a_string_array_are_kept (``"string"``),
    test_a_blank_date_is_absent (the format),
    test_a_blank_the_choices_leave_out_is_absent (the choices, whole),
    test_a_blank_string_is_kept (``choices is None``), and
    test_a_blank_the_choices_name_is_kept (``"" in choices``).
    """
    return (
        (json_type is None or json_type == "string")
        and fmt is None
        and (param.choices is None or "" in param.choices)
    )


def _form(request: HttpRequest) -> QueryDict:
    """The form fields a request's body carries, whatever its method."""
    if request.content_type not in (_URLENCODED, _MULTIPART):
        # Readable even once ``request.POST`` has been, as CsrfViewMiddleware
        # reads it: Django leaves the stream unread for a body it does not parse.
        if request.body:
            raise UnsupportedMediaType(
                gettext('Unsupported media type "%(media_type)s" in request.')
                % {"media_type": request.content_type}
            )
        return QueryDict(encoding=request.encoding)
    if request.method == "POST":
        # Django's own parse, which CsrfViewMiddleware may already have done:
        # the stream can be read once, and ``request.POST`` is where it went.
        return request.POST
    if request.content_type == _URLENCODED:
        return QueryDict(request.body, encoding=request.encoding)
    # The parser Django runs for a POST, over the buffered body. Its files
    # are discarded, since no parameter can declare one.
    fields, _files = MultiPartParser(
        request.META, BytesIO(request.body), request.upload_handlers, request.encoding
    ).parse()
    return fields


def _json_object(request: HttpRequest) -> dict[str, Any]:
    """A JSON body as the object it must be, or the refusal a malformed one is."""
    if not request.body:
        return {}
    try:
        decoded = json.loads(request.body, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        # JSONDecodeError and UnicodeDecodeError are both ValueErrors, and so
        # is _refuse_constant's, and the bare one an integer longer than the
        # 4300 digits Python converts from a string by default raises; each
        # means the body is not JSON. RecursionError is no ValueError: it is a body
        # nested deeper than the decoder reaches, and nothing above this
        # catches it, so uncaught it is the host's 500 for a client's body.
        # One branch to coverage, so each type is held by its own test:
        # test_a_malformed_body_is_refused_under_non_field_errors (ValueError)
        # and test_a_body_nested_past_the_decoder_is_refused_under_non_field_errors
        # (RecursionError).
        raise InvalidArguments(
            {NON_FIELD_ERRORS: [gettext("The request body is not valid JSON.")]}
        ) from None
    if not isinstance(decoded, dict):
        raise InvalidArguments({NON_FIELD_ERRORS: [expected_type("object")]})
    return decoded


def _refuse_constant(constant: str) -> NoReturn:
    """Refuse ``NaN`` and the infinities, which Python's decoder takes and JSON does not have.

    No JSON encoder writes one, and ``coerce_flat`` refuses the same values on
    the flat route. DRF's parser refuses them too, in its default strict mode.
    An overflowing literal such as ``1e400`` is well-formed and decodes to an
    infinity all the same; the shape check refuses that one at its address
    wherever the declaration reaches. Inside a container it leaves open,
    nothing is declared, so there it is the Validator's, as every rule is.
    """
    raise ValueError(f"{constant} is not JSON.")
