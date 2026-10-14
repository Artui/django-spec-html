"""``request_arguments`` - a Django request, read as the arguments a JSON caller would send."""

from __future__ import annotations

import json
from collections.abc import Mapping
from io import BytesIO
from typing import Any, NoReturn

from django.http import HttpRequest, QueryDict
from django.http.multipartparser import MultiPartParser
from django.utils.datastructures import MultiValueDict
from django.utils.translation import gettext

from django_service_specs.http.route_arguments import route_arguments
from django_service_specs.http.unsupported_media_type import UnsupportedMediaType
from django_service_specs.parameters.coded_message import CodedMessage
from django_service_specs.parameters.coerce_flat import coerce_flat
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.parameters.non_field_errors import NON_FIELD_ERRORS
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.parameters.utils import (
    expected_type,
    max_length_in_force,
)

_CSRF_FIELD = "csrfmiddlewaretoken"
"""The field ``{% csrf_token %}`` renders into a form: Django's, and never an argument."""

_COMMA_SEPARABLE = ("number", "integer", "boolean")
"""The element types a comma can never be part of, so ``?ids=1,2`` is two elements, not one.

A string can hold a comma, so a string array takes a value whole, unless its
format or its choices say otherwise: a decimal string never holds one, and
nor does a value its comma-free choices list. See ``_splits``."""

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
      as DRF's parser reads one. Its values are not coerced here: a ``"5"``
      sent for an integer is the shape check's to read, as it reads one from
      any caller.
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
    the last, as ``QueryDict`` reads a repeated key. **An array of numbers,
    integers, booleans or decimal strings also reads a value holding commas
    as its elements**, ``?ids=1,2`` as ``[1, 2]`` with the space around a
    part trimmed, because a comma cannot be part of such a value and it is
    the spelling a django-filter CSV filter advertises; repeated keys and
    commas may mix. A decimal element stays the string it spells. An
    empty part (``?ids=1,,2``) is not dropped as a blank element is: it is
    refused at its index as any malformed element is. A string array is
    split only where its choices hold no comma, as an ordering's do, so
    ``?o=title,-pk`` is ``["title", "-pk"]``; otherwise it is never split,
    since a comma is part of a string. **An array is read no further than one
    element past the most it takes** - its ``max_length``, or the
    ``SERVICE_SPECS_MAX_ARRAY_LENGTH`` setting's default - so a list over it is
    refused at its name as a whole, without a million commas becoming a
    million elements first; Django's ``DATA_UPLOAD_MAX_NUMBER_FIELDS`` counts
    keys, and a comma list is one. **A blank value is
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

    **An upload is an argument**, under the name its part carries, as each
    field is: the upload itself, Django's ``UploadedFile``, for a parameter of
    type ``file``, every upload under the name for an array, and the last
    of repeated ones for anything else. It replaces a field of the same name,
    since a browser sends a file input only as a file: a single value reads as
    the upload in DRF's merged data too, where an array there would hold the
    field and the uploads together. A POST's uploads are Django's own ``request.FILES``; those in a
    PUT, PATCH or DELETE body are parsed here with its fields. An upload under
    a name ``parameters`` do not declare passes through like an undeclared
    field, for the closed argument set to refuse, and a file input left empty
    is no upload, so it is absent. Only a multipart body carries one.

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
            [`route_arguments`][django_service_specs.http.route_arguments.route_arguments].
    """
    route = route_arguments(parameters, url_kwargs)
    if request.method in ("GET", "HEAD"):
        return coerce_flat(parameters, {**_flat(parameters, request.GET), **route})
    if request.content_type == "application/json":
        return {**_json_object(request), **coerce_flat(parameters, route)}
    fields, uploads = _form(request)
    return coerce_flat(
        parameters, {**_flat(parameters, fields), **_uploads(parameters, uploads), **route}
    )


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
            # The element's format too, which ``Parameter`` takes on an array
            # for its element alone: test_blank_elements_of_a_decimal_array_are_dropped.
            keep = _takes_blank(param.items, param, fmt=param.format)
            # The element type and its choices decide whether a value is split,
            # and _splits names the test holding each condition. A blank is
            # dropped first, so an empty part left by a stray comma is the
            # reader's malformed element rather than a blank.
            split = _splits(param)
            # Built no further than one element past the most the array takes,
            # which is still past it, so ``coerce_flat`` refuses the list at its
            # name as it would the whole: a value of a million commas is never
            # split a million times. One past rather than at it, or a list over
            # the bound would be cut to one that is accepted.
            most = max_length_in_force(param)
            values: list[str] = []
            for value in source.getlist(key):
                # One branch to coverage, each condition held by
                # test_a_flat_array_is_split_no_further_than_one_past_its_maximum:
                # [repeated-keys] the comparison, and the setting's ``None`` by
                # test_a_setting_of_none_splits_every_comma, which compares
                # nothing.
                if most is not None and len(values) > most:
                    break
                # One branch to coverage, so each condition is held by its own
                # test: test_blank_elements_of_a_non_string_array_are_dropped
                # (the blank: without it every element is dropped) and
                # test_blank_elements_of_a_string_array_are_kept (``not keep``).
                if value == "" and not keep:
                    continue
                if split:
                    # Split no further than the room left: [one-value] and
                    # [mixed] of the test above, and at the bound ``0``, which
                    # leaves the value whole and one past.
                    room = -1 if most is None else most - len(values)
                    values.extend(part.strip() for part in value.split(",", room))
                else:
                    values.append(value)
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


def _splits(param: Parameter) -> bool:
    """Whether a comma in one of the array's values separates elements, so ``?o=a,-b`` is two.

    An element type that can never hold a comma splits, and so does a
    decimal string, which never holds one either: it is what a django-filter
    CSV number filter declares, whose spelling is ``?pks=1,2``. So does an
    array of strings whose declared choices hold none: every element must be
    one of them, so a value holding a comma can only be several, and refused
    whole it would refuse the spelling django-filter's ``OrderingFilter``
    takes, ``?o=title,-pk``. Without either, or with one choice that holds a
    comma, a string array takes each value whole, since a comma may be part
    of it. Repeated keys make a list either way, and the two may mix.

    The format is read alone, with no ``"string"`` beside it: ``Parameter``
    takes a format on an array only for a string element, and only
    ``decimal``.

    One branch to coverage per return, so each condition is held by its own
    test: test_a_number_array_reads_a_comma_separated_value_as_its_elements
    (the types that split),
    test_a_decimal_array_reads_a_comma_separated_value_as_its_elements_exactly
    (the format),
    test_an_array_with_no_declared_element_is_not_split_by_its_choices
    (``"string"``: without it an undeclared element with comma-free choices
    would split), test_a_string_array_is_not_split (the choices declared),
    test_a_string_array_whose_choices_hold_a_comma_is_not_split (none holds a
    comma), and test_a_string_array_whose_choices_hold_no_comma_is_split.
    """
    if param.items in _COMMA_SEPARABLE or param.format == "decimal":
        return True
    return (
        param.items == "string"
        and param.choices is not None
        and not any("," in str(choice) for choice in param.choices)
    )


def _takes_blank(json_type: object, param: Parameter, *, fmt: str | None = None) -> bool:
    """Whether ``""`` is a value of ``json_type`` under ``param``, so a blank is kept rather than absent.

    ``json_type`` is the parameter's own type, or an array's element type:
    ``None`` for an undeclared element, which ``coerce_flat`` leaves as the
    string it arrived as, and a ``Parameters`` for a row, which is no string.
    An ``"any"`` parameter is untyped, and an untyped read is handed what was
    sent, so its blank is kept as ``""`` as a string's is.
    A format names what a string decodes into, and no date or decimal is
    blank. ``choices`` constrain a scalar and each element of an array alike,
    so a blank they leave out is refused by the shape check rather than read
    as a choice, and here it is absent instead.

    One branch to coverage, so each condition is held by its own test:
    test_blank_elements_of_an_undeclared_element_type_are_kept (``None``),
    test_blank_elements_of_a_string_array_are_kept (``"string"``),
    test_a_blank_untyped_read_is_kept (``"any"``),
    test_a_blank_date_is_absent (the format),
    test_a_blank_the_choices_leave_out_is_absent (the choices, whole),
    test_a_blank_string_is_kept (``choices is None``), and
    test_a_blank_the_choices_name_is_kept (``"" in choices``).
    """
    return (
        (json_type is None or json_type in ("string", "any"))
        and fmt is None
        and (param.choices is None or "" in param.choices)
    )


def _uploads(parameters: Parameters, source: MultiValueDict) -> dict[str, Any]:
    """A body's uploads, by name: every one for an array, the last for anything else."""
    # One branch to coverage, so each condition is held by its own test:
    # test_an_undeclared_upload_passes_through_for_the_closed_set_to_refuse
    # (the declaration) and test_a_file_takes_the_last_of_repeated_uploads (the
    # array: without it every upload becomes a list).
    return {
        key: source.getlist(key)
        if (param := parameters.get(key)) is not None and param.type == "array"
        else source[key]
        for key in source
    }


def _form(request: HttpRequest) -> tuple[QueryDict, MultiValueDict]:
    """The form fields and the uploads a request's body carries, whatever its method."""
    if request.content_type not in (_URLENCODED, _MULTIPART):
        # Readable even once ``request.POST`` has been, as CsrfViewMiddleware
        # reads it: Django leaves the stream unread for a body it does not parse.
        if request.body:
            raise UnsupportedMediaType(
                gettext('Unsupported media type "%(media_type)s" in request.')
                % {"media_type": request.content_type}
            )
        return QueryDict(encoding=request.encoding), MultiValueDict()
    if request.method == "POST":
        # Django's own parse, which CsrfViewMiddleware may already have done:
        # the stream can be read once, and ``request.POST`` and
        # ``request.FILES`` are where it went.
        return request.POST, request.FILES
    if request.content_type == _URLENCODED:
        return QueryDict(request.body, encoding=request.encoding), MultiValueDict()
    # The parser Django runs for a POST, which hands back the uploads beside
    # the fields, fed from the request's own stream while nothing has read it.
    # Read whole first, a body past DATA_UPLOAD_MAX_MEMORY_SIZE was refused
    # with RequestDataTooBig, where the parser streams a file part to the
    # upload handlers and bounds only the fields, so a PATCH refused the
    # upload a POST took. Once the stream has been read, ``request.body`` is
    # the buffer if something kept one, and raises RawPostDataException if
    # not: parsing the spent stream would read no arguments at all and say
    # nothing, where Django's own POST parse does the same.
    # test_an_upload_past_the_body_size_limit_is_read_whatever_the_method
    # holds the stream, and test_a_multipart_stream_read_without_a_buffer_is_refused
    # the read one; a buffered body would parse from either, since Django
    # rewinds the stream onto the buffer it keeps. ``_read_started`` is the flag Django's own POST parse
    # reads to make the same choice; the stubs do not declare it.
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
            # DRF's parser refuses it with a ParseError, whose code this is:
            # test_a_body_that_is_no_json_is_refused_as_drf_s_parser_refuses_it.
            {
                NON_FIELD_ERRORS: [
                    CodedMessage(gettext("The request body is not valid JSON."), "parse_error")
                ]
            }
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
