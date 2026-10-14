"""``request_arguments``: a Django request read as the arguments a JSON caller would send.

Every request here comes from Django's own ``RequestFactory``, which encodes a
query string, a form and a multipart body the way a browser does, so what is
read back is what a real client's request carries rather than a double's idea
of it.
"""

from __future__ import annotations

import contextlib
import importlib
import json
from typing import Any

import django_filters
import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured, RequestDataTooBig
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile
from django.http import HttpRequest
from django.http.request import RawPostDataException
from django.test import RequestFactory, override_settings
from django.test.client import encode_multipart

from django_service_specs.adapters.forms.form_validator import FormValidator
from django_service_specs.filters.filter_set_parameters import filter_set_parameters
from django_service_specs.http.request_arguments import request_arguments
from django_service_specs.http.unsupported_media_type import UnsupportedMediaType
from django_service_specs.parameters.check_arguments import check_arguments
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.parameters.non_field_errors import NON_FIELD_ERRORS
from django_service_specs.parameters.parameter import Parameter
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.selectors.shape_queryset import shape_queryset
from django_service_specs.specs.selector_kind import SelectorKind
from django_service_specs.specs.selector_spec import SelectorSpec
from tests.dispatch.utils import make_user
from tests.dispatch_app.models import Note
from tests.http.utils import DEEP_JSON
from tests.parameters.utils import coded

# The module, which ``http``'s own ``request_arguments`` re-export shadows as an attribute.
request_arguments_module = importlib.import_module("django_service_specs.http.request_arguments")

FACTORY = RequestFactory()
FORM = "application/x-www-form-urlencoded"
NOT_JSON = "The request body is not valid JSON."
BOUNDARY = "BoUnDaRy"

FLAT = Parameters.of(
    Parameter("pk", "integer"),
    Parameter("count", "integer"),
    Parameter("active", "boolean"),
    Parameter("title", "string"),
    Parameter("ids", "array", items="integer"),
    Parameter("words", "array", items="string"),
    Parameter("tags", "array"),
)
BOOK = Parameters.of(Parameter("title", "string", required=True))
BLANKS = Parameters.of(
    Parameter("since", "string", format="date"),
    Parameter("price", "string", format="decimal"),
    Parameter("status", "string", choices=("draft", "live")),
    Parameter("shelf", "string", choices=("", "top")),
    Parameter("labels", "array", items="string", choices=("a", "b")),
)


def files() -> Parameters:
    """``FLAT`` and two uploads, built per test so the module imports where no file type exists."""
    return Parameters(
        (*FLAT, Parameter("notes", "file"), Parameter("scans", "array", items="file"))
    )


def contents(arguments: dict[str, Any]) -> dict[str, Any]:
    """``arguments`` with each upload read, so a test compares what the file holds."""
    return {
        key: [one.read() for one in value]
        if isinstance(value, list) and value and isinstance(value[0], UploadedFile)
        else value.read()
        if isinstance(value, UploadedFile)
        else value
        for key, value in arguments.items()
    }


def read(request: HttpRequest, parameters: Parameters = FLAT, **kwargs: Any) -> dict[str, Any]:
    return request_arguments(request, parameters, **kwargs)


def refusal(request: HttpRequest, parameters: Parameters = FLAT, **kwargs: Any) -> dict[Any, Any]:
    with pytest.raises(InvalidArguments) as caught:
        request_arguments(request, parameters, **kwargs)
    return caught.value.detail


def post_json(payload: Any, method: str = "post") -> HttpRequest:
    return getattr(FACTORY, method)("/", data=json.dumps(payload), content_type="application/json")


class TestQueryString:
    def test_a_scalar_takes_the_last_of_repeated_values(self) -> None:
        # QueryDict's own reading of a repeated key, so a query string built by
        # appending reads as the client's last word rather than its first.
        assert read(FACTORY.get("/?count=1&count=2&title=a&title=b")) == {
            "count": 2,
            "title": "b",
        }

    def test_an_array_takes_every_value_and_coerces_each(self) -> None:
        assert read(FACTORY.get("/?ids=1&ids=2&ids=3")) == {"ids": [1, 2, 3]}

    def test_a_single_value_for_an_array_is_a_one_element_list(self) -> None:
        assert read(FACTORY.get("/?ids=7")) == {"ids": [7]}

    def test_head_reads_the_query_string_too(self) -> None:
        assert read(FACTORY.head("/?count=3&active=true")) == {"count": 3, "active": True}

    def test_a_value_that_does_not_parse_is_refused_at_its_name(self) -> None:
        assert refusal(FACTORY.get("/?count=many&ids=1&ids=x")) == {
            "count": ["Enter a whole number."],
            "ids": {1: ["Enter a whole number."]},
        }

    def test_a_nested_parameter_is_refused_by_name(self) -> None:
        parameters = Parameters.of(Parameter("books", "array", items=BOOK))
        assert refusal(FACTORY.get("/?books=Lathe"), parameters) == {
            "books": ["This argument has nested parameters, which a flat transport cannot send."]
        }

    def test_an_undeclared_key_passes_through_for_the_closed_set_to_refuse(self) -> None:
        # Left to check_arguments, under the caller's policy; the blank one is
        # kept too, since a blank only means "absent" for a declared type.
        assert read(FACTORY.get("/?colour=red&shade=")) == {"colour": "red", "shade": ""}


CSV = Parameters.of(
    Parameter("pks", "array", items="number"),
    Parameter("ids", "array", items="integer"),
    Parameter("flags", "array", items="boolean"),
    Parameter("words", "array", items="string"),
    Parameter("tags", "array"),
    Parameter("books", "array", items=BOOK),
    # An ordering, as django-filter's OrderingFilter offers it: no choice
    # holds a comma, so a comma can only be separating two of them.
    Parameter("o", "array", items="string", choices=("title", "-title", "pk", "-pk")),
    # Choices that hold a comma, so a comma may be part of one.
    Parameter("places", "array", items="string", choices=("Paris, France", "Paris")),
    # Choices with no element type: whatever an element is, it is not read as
    # a string the declaration promised, so it is taken whole.
    Parameter("marks", "array", choices=("a", "b")),
)


def _decimal_pks() -> Parameters:
    """A CSV number filter's declaration, built in the test so a tree refusing it fails there."""
    return Parameters.of(Parameter("pks", "array", items="string", format="decimal"))


class _Ordered(django_filters.FilterSet):
    o = django_filters.OrderingFilter(fields=["title", "pk"])


class TestCommaSeparatedArrays:
    """``?pks=1,2``: the spelling django-filter's CSV filters advertise.

    Splitting is the element type's to allow. A number, an integer and a
    boolean can never hold a comma, so the split is unambiguous; a string
    can, so a string array (declared or not) takes its value whole, unless
    its choices hold no comma, when a value holding one can only be several
    choices. ``_splits`` names the test holding each condition.
    """

    def test_a_number_array_reads_a_comma_separated_value_as_its_elements(self) -> None:
        assert read(FACTORY.get("/?pks=1,2.5"), CSV) == {"pks": [1.0, 2.5]}

    def test_an_integer_array_reads_a_comma_separated_value_as_its_elements(self) -> None:
        assert read(FACTORY.get("/?ids=1,2,3"), CSV) == {"ids": [1, 2, 3]}

    def test_a_boolean_array_reads_a_comma_separated_value_as_its_elements(self) -> None:
        assert read(FACTORY.get("/?flags=true,0,False"), CSV) == {"flags": [True, False, False]}

    def test_space_around_a_part_is_trimmed(self) -> None:
        # A boolean is the type that cares: " true" is no spelling of one,
        # where an integer already ignores the space.
        assert read(FACTORY.get("/?flags=true, false ,1"), CSV) == {"flags": [True, False, True]}

    def test_repeated_keys_still_make_a_list(self) -> None:
        assert read(FACTORY.get("/?ids=1&ids=2"), CSV) == {"ids": [1, 2]}

    def test_repeated_keys_and_commas_mix_with_each_value_split(self) -> None:
        assert read(FACTORY.get("/?ids=1,2&ids=3&ids=4,5"), CSV) == {"ids": [1, 2, 3, 4, 5]}

    def test_a_form_body_splits_too(self) -> None:
        request = FACTORY.post("/", data="ids=1,2&pks=3", content_type=FORM)
        assert read(request, CSV) == {"ids": [1, 2], "pks": [3.0]}

    def test_a_decimal_array_reads_a_comma_separated_value_as_its_elements_exactly(self) -> None:
        # A decimal never holds a comma, so ``?pks=1,2`` splits as a number's
        # does, and each part stays the string it spells, digits and all.
        fraction = "0.1234567890123456789012345678901234567891"
        query = f"/?pks=1,{fraction}, 3 &pks=4"
        assert read(FACTORY.get(query), _decimal_pks()) == {"pks": ["1", fraction, "3", "4"]}

    def test_blank_elements_of_a_decimal_array_are_dropped(self) -> None:
        # No decimal is blank, so a blank element is absent, as a number's is.
        assert read(FACTORY.get("/?pks=&pks=1"), _decimal_pks()) == {"pks": ["1"]}
        assert read(FACTORY.get("/?pks="), _decimal_pks()) == {}

    def test_a_string_array_is_not_split(self) -> None:
        # A comma is part of a string; the whole value is the element.
        assert read(FACTORY.get("/?words=a,b&words=c"), CSV) == {"words": ["a,b", "c"]}

    def test_a_string_array_whose_choices_hold_no_comma_is_split(self) -> None:
        # ``?o=title,-pk``: the spelling django-filter's OrderingFilter takes. No
        # choice holds a comma, so a value holding one is several choices.
        assert read(FACTORY.get("/?o=title,-pk&o=pk"), CSV) == {"o": ["title", "-pk", "pk"]}

    def test_a_string_array_whose_choices_hold_a_comma_is_not_split(self) -> None:
        # One choice holds a comma, so the whole value may be that choice.
        assert read(FACTORY.get("/?places=Paris, France&places=Paris"), CSV) == {
            "places": ["Paris, France", "Paris"]
        }

    def test_an_array_with_no_declared_element_is_not_split_by_its_choices(self) -> None:
        assert read(FACTORY.get("/?marks=a,b"), CSV) == {"marks": ["a,b"]}

    @pytest.mark.django_db
    @pytest.mark.parametrize("query", ["o=title,-pk", "o=title&o=-pk"])
    def test_an_ordering_by_two_fields_orders_the_rows(self, query: str) -> None:
        ada = make_user("ada")
        rows = [Note.objects.create(owner=ada, title=title) for title in ("b", "a", "a")]
        params = filter_set_parameters(_Ordered)
        checked = check_arguments(params, read(FACTORY.get(f"/?{query}"), params))
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda: None, filter_set=_Ordered)
        ordered = shape_queryset(Note.objects.all(), spec, checked, source_label="x")
        assert list(ordered.values_list("pk", flat=True)) == [rows[2].pk, rows[1].pk, rows[0].pk]

    def test_an_array_with_no_declared_element_is_not_split(self) -> None:
        assert read(FACTORY.get("/?tags=a,b"), CSV) == {"tags": ["a,b"]}

    def test_an_array_of_rows_is_not_split_and_stays_refused_by_name(self) -> None:
        assert refusal(FACTORY.get("/?books=a,b"), CSV) == {
            "books": ["This argument has nested parameters, which a flat transport cannot send."]
        }

    @pytest.mark.parametrize(
        ("query", "index"),
        [("ids=1,,3", 1), ("ids=1,", 1), ("ids=,1", 0), ("ids=1, ,3", 1)],
    )
    def test_an_empty_part_is_refused_at_its_index_as_a_malformed_element(
        self, query: str, index: int
    ) -> None:
        # Not dropped as a blank element is: a stray comma is a mistake in the
        # value, and the reader's wording for one is the element's own.
        assert refusal(FACTORY.get(f"/?{query}"), CSV) == {
            "ids": {index: ["Enter a whole number."]}
        }

    def test_the_refused_part_is_named_by_its_index_in_the_whole_list(self) -> None:
        assert refusal(FACTORY.get("/?ids=1&ids=2,,3"), CSV) == {
            "ids": {2: ["Enter a whole number."]}
        }

    def test_a_part_that_does_not_parse_is_refused_at_its_index(self) -> None:
        assert refusal(FACTORY.get("/?ids=1,x,2.5&flags=true,maybe&pks=1,y"), CSV) == {
            "ids": {1: ["Enter a whole number."], 2: ["Enter a whole number."]},
            "flags": {1: ["Enter true, false, 1 or 0."]},
            "pks": {1: ["Enter a number."]},
        }

    def test_a_blank_value_is_still_absent(self) -> None:
        assert read(FACTORY.get("/?ids=&ids=1,2"), CSV) == {"ids": [1, 2]}
        assert read(FACTORY.get("/?ids="), CSV) == {}


class TestBlanks:
    def test_a_blank_value_for_a_type_that_is_not_a_string_is_absent(self) -> None:
        # Without the rule, ``""`` reaches coerce_flat as an integer and is
        # refused as "Enter a whole number.", which is not what a left-blank
        # field means.
        assert read(FACTORY.get("/?count=&active=&pk=4")) == {"pk": 4}

    def test_a_blank_string_is_kept(self) -> None:
        # Holds ``type != "string"`` in the blank guard: a string's blank is a
        # value, the empty string, and dropping it would make it unsendable.
        assert read(FACTORY.get("/?title=")) == {"title": ""}

    def test_a_blank_untyped_read_is_kept(self) -> None:
        # Holds ``"any"`` in the blank guard: an untyped read is handed what
        # was sent, so ``?pk=`` reaches it as the empty string, as a string's
        # does, rather than being dropped. Built in the body, so the module
        # imports on a release whose Parameter has no such type.
        untyped = Parameters.of(Parameter("pk", "any"))
        assert read(FACTORY.get("/?pk="), untyped) == {"pk": ""}

    def test_blank_elements_of_a_non_string_array_are_dropped(self) -> None:
        assert read(FACTORY.get("/?ids=1&ids=&ids=3")) == {"ids": [1, 3]}

    def test_an_array_whose_every_element_is_blank_is_absent(self) -> None:
        # A multi-value input left blank: nothing was chosen, so nothing is
        # sent, and a required array reads as missing rather than as ``[]``.
        assert read(FACTORY.get("/?ids=&ids=")) == {}

    def test_blank_elements_of_a_string_array_are_kept(self) -> None:
        # Holds the ``"string"`` half of the element check.
        assert read(FACTORY.get("/?words=a&words=")) == {"words": ["a", ""]}

    def test_a_blank_date_is_absent(self) -> None:
        # A filter form's empty date input. Kept, it reached the shape check as
        # a malformed date, refusing a form that was only left blank.
        assert read(FACTORY.get("/?since=&price="), BLANKS) == {}

    def test_a_blank_the_choices_leave_out_is_absent(self) -> None:
        # An "any status" option whose value is blank: the shape check would
        # refuse it as no valid choice, and an array's choices constrain each
        # of its elements the same way.
        assert read(FACTORY.get("/?status=&labels=&labels=a"), BLANKS) == {"labels": ["a"]}

    def test_a_blank_the_choices_name_is_kept(self) -> None:
        assert read(FACTORY.get("/?shelf="), BLANKS) == {"shelf": ""}

    def test_blank_elements_of_an_undeclared_element_type_are_kept(self) -> None:
        # Holds the ``None`` half: coerce_flat reads an undeclared element as
        # the string it arrived as, so its blank is a string's blank.
        assert read(FACTORY.get("/?tags=&tags=x")) == {"tags": ["", "x"]}


class TestFormBody:
    def test_a_form_encoded_post_is_flat(self) -> None:
        request = FACTORY.post("/", data="count=2&ids=1&ids=2&title=", content_type=FORM)
        assert read(request) == {"count": 2, "ids": [1, 2], "title": ""}

    def test_the_csrf_token_is_never_an_argument(self) -> None:
        request = FACTORY.post("/", data={"csrfmiddlewaretoken": "t0k3n", "count": "2"})
        assert read(request) == {"count": 2}

    def test_a_multipart_post_is_flat_and_its_uploads_are_arguments(self) -> None:
        attached = SimpleUploadedFile("notes.txt", b"A file.")
        upload = FACTORY.post("/", data={"count": "2", "ids": ["1", "2"], "notes": attached})
        assert upload.content_type == "multipart/form-data"
        assert contents(read(upload, files())) == {"count": 2, "ids": [1, 2], "notes": b"A file."}

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_a_form_encoded_body_is_read_whatever_the_method(self, method: str) -> None:
        # Django parses a form body into ``request.POST`` for POST alone, so a
        # PUT or PATCH from a script (or an htmx ``hx-put``) would otherwise
        # arrive with no arguments at all and fail as "required".
        request = getattr(FACTORY, method)("/", data="count=2&ids=1&ids=2", content_type=FORM)
        assert request.POST == {}
        assert read(request) == {"count": 2, "ids": [1, 2]}

    def test_a_multipart_body_is_read_whatever_the_method(self) -> None:
        attached = SimpleUploadedFile("notes.txt", b"A file.")
        body = encode_multipart(BOUNDARY, {"count": "2", "ids": ["1", "2"], "notes": attached})
        request = FACTORY.patch(
            "/", data=body, content_type=f"multipart/form-data; boundary={BOUNDARY}"
        )
        assert request.POST == {}
        # Django fills ``request.FILES`` for POST alone, so the upload is read
        # from the body here rather than dropped with the parser's other half.
        assert not request.FILES
        assert contents(read(request, files())) == {"count": 2, "ids": [1, 2], "notes": b"A file."}

    @pytest.mark.parametrize("method", ["post", "put"])
    def test_a_body_that_is_neither_form_nor_json_is_refused(self, method: str) -> None:
        # Read as no arguments, an operation whose parameters are all optional
        # ran with nothing and answered success; read as a form, ``count=2``
        # became an argument its client never framed as one.
        request = getattr(FACTORY, method)(
            "/", data='{"count": 2}', content_type="application/merge-patch+json"
        )
        with pytest.raises(UnsupportedMediaType) as caught:
            read(request)
        assert caught.value.message == (
            'Unsupported media type "application/merge-patch+json" in request.'
        )

    def test_the_refusal_survives_the_middleware_reading_post_first(self) -> None:
        # CsrfViewMiddleware reads ``request.POST`` before any view runs, and a
        # body it has consumed could no longer be seen to be there.
        request = FACTORY.post("/", data="count=2", content_type="text/plain")
        assert request.POST == {}
        with pytest.raises(UnsupportedMediaType):
            read(request)

    def test_a_multipart_post_is_read_after_the_middleware_has_read_it(self) -> None:
        # Django streams a multipart POST into ``request.POST`` without keeping
        # the body, so once CsrfViewMiddleware has read it, ``request.POST`` is
        # the only place its fields still are: parsing the body again raises.
        attached = SimpleUploadedFile("notes.txt", b"A file.")
        request = FACTORY.post("/", data={"count": "2", "notes": attached})
        assert request.POST["count"] == "2"
        assert contents(read(request, files())) == {"count": 2, "notes": b"A file."}

    def test_a_request_with_no_body_carries_no_arguments_whatever_its_label(self) -> None:
        # ``fetch(url, {method: "POST"})`` for an operation its route names.
        request = FACTORY.post("/", data="", content_type="text/plain")
        assert read(request, url_kwargs={"pk": "4"}) == {"pk": 4}

    def test_a_post_does_not_read_its_query_string(self) -> None:
        request = FACTORY.post("/?count=9", data="title=x", content_type=FORM)
        assert read(request) == {"title": "x"}


class TestUploads:
    def test_a_declared_file_is_the_upload_itself(self) -> None:
        attached = SimpleUploadedFile("notes.txt", b"A file.", content_type="text/plain")
        arguments = read(FACTORY.post("/", data={"notes": attached}), files())
        assert isinstance(arguments["notes"], UploadedFile)
        assert arguments["notes"].name == "notes.txt"
        assert contents(arguments) == {"notes": b"A file."}

    def test_an_array_of_files_takes_every_upload_under_its_key(self) -> None:
        scans = [SimpleUploadedFile("a.png", b"A."), SimpleUploadedFile("b.png", b"B.")]
        assert contents(read(FACTORY.post("/", data={"scans": scans}), files())) == {
            "scans": [b"A.", b"B."]
        }

    def test_a_file_takes_the_last_of_repeated_uploads(self) -> None:
        # The way a repeated field's scalar reads, and QueryDict's own reading.
        notes = [SimpleUploadedFile("a.txt", b"A."), SimpleUploadedFile("b.txt", b"B.")]
        assert contents(read(FACTORY.post("/", data={"notes": notes}), files())) == {"notes": b"B."}

    def test_an_upload_wins_over_a_field_of_the_same_name(self) -> None:
        # A browser sends a file input's part as a file, never as a field, so a
        # field under the same name is something else, and the upload is what
        # the parameter declares.
        attached = SimpleUploadedFile("notes.txt", b"A file.")
        request = FACTORY.generic(
            "PUT",
            "/",
            data=_merged_multipart({"notes": "notes.txt"}, {"notes": attached}),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
        )
        assert contents(read(request, files())) == {"notes": b"A file."}

    def test_an_undeclared_upload_passes_through_for_the_closed_set_to_refuse(self) -> None:
        attached = SimpleUploadedFile("notes.txt", b"A file.")
        assert contents(read(FACTORY.post("/", data={"minutes": attached}))) == {
            "minutes": b"A file."
        }

    def test_a_form_encoded_body_carries_no_file(self) -> None:
        # Nothing to read: urlencoded has no upload part, and the declared
        # file is simply absent rather than a crash on a parser that is not run.
        request = FACTORY.put("/", data="count=2", content_type=FORM)
        assert read(request, files()) == {"count": 2}

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_an_upload_past_the_body_size_limit_is_read_whatever_the_method(
        self, method: str
    ) -> None:
        # DATA_UPLOAD_MAX_MEMORY_SIZE (2.5 MB by default) bounds a body read
        # whole into memory, and Django never applies it to a file part, which
        # its parser streams to the upload handlers. Reading a PATCH's body
        # whole to parse it refused the upload a POST took with
        # RequestDataTooBig.
        scan = b"x" * (3 * 1024 * 1024)
        request = FACTORY.generic(
            method,
            "/",
            data=encode_multipart(BOUNDARY, {"count": "2", "notes": SimpleUploadedFile("a", scan)}),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
        )
        assert contents(read(request, files())) == {"count": 2, "notes": scan}

    @pytest.mark.parametrize("method", ["POST", "PATCH"])
    def test_a_field_past_the_body_size_limit_is_still_refused(self, method: str) -> None:
        # The limit bounds what is read into memory, and a form field is: only
        # a file part streams past it.
        request = FACTORY.generic(
            method,
            "/",
            data=encode_multipart(BOUNDARY, {"title": "x" * (3 * 1024 * 1024)}),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
        )
        with pytest.raises(RequestDataTooBig):
            read(request)

    def test_a_multipart_body_already_read_is_parsed_from_what_was_read(self) -> None:
        # Something before the view read ``request.body``, so the stream is
        # spent and the buffer is where the body still is, as Django's own
        # parse of a POST takes it.
        attached = SimpleUploadedFile("notes.txt", b"A file.")
        request = FACTORY.patch(
            "/",
            data=encode_multipart(BOUNDARY, {"count": "2", "notes": attached}),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
        )
        assert request.body
        assert contents(read(request, files())) == {"count": 2, "notes": b"A file."}

    def test_a_multipart_body_parsed_from_the_stream_cannot_be_read_again(self) -> None:
        # Django's own POST parse leaves the body the same way: the stream
        # went to the parser and nothing kept a copy. A reader after the view
        # - middleware, an audit log, an error reporter - meets this.
        request = FACTORY.patch(
            "/",
            data=encode_multipart(BOUNDARY, {"count": "2"}),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
        )
        assert read(request) == {"count": 2}
        with pytest.raises(RawPostDataException):
            request.body  # noqa: B018
        with pytest.raises(RawPostDataException):
            read(request)

    def test_a_multipart_body_read_first_stays_readable_and_parses_again(self) -> None:
        # Reading ``request.body`` before the parse keeps the buffer, which the
        # parse then reads from, so both the body and the arguments survive.
        body = encode_multipart(BOUNDARY, {"count": "2"})
        request = FACTORY.patch(
            "/", data=body, content_type=f"multipart/form-data; boundary={BOUNDARY}"
        )
        assert request.body == body
        assert read(request) == {"count": 2}
        assert read(request) == {"count": 2}
        assert request.body == body

    def test_reading_the_body_first_brings_the_size_limit_back_on_an_upload(self) -> None:
        # The way round the spent stream is a whole-body read, which
        # DATA_UPLOAD_MAX_MEMORY_SIZE bounds, file parts and all; the parse
        # itself takes the same upload, as the test above it shows.
        request = FACTORY.patch(
            "/",
            data=encode_multipart(BOUNDARY, {"notes": SimpleUploadedFile("a", b"x" * 3 * 1024**2)}),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
        )
        with pytest.raises(RequestDataTooBig):
            request.body  # noqa: B018

    def test_a_multipart_stream_read_without_a_buffer_is_refused(self) -> None:
        # Something read the stream and kept nothing, so the body is gone:
        # parsed anyway, it would be a call with no arguments.
        request = FACTORY.patch(
            "/",
            data=encode_multipart(BOUNDARY, {"count": "2"}),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
        )
        request.read()
        with pytest.raises(RawPostDataException):
            read(request)

    def test_a_file_input_left_empty_is_absent(self) -> None:
        # A browser posts an empty file input as a part with no filename, and
        # Django's parser skips it, so a required file reads as missing.
        body = (
            f"--{BOUNDARY}\r\n"
            'Content-Disposition: form-data; name="notes"; filename=""\r\n'
            "Content-Type: application/octet-stream\r\n\r\n\r\n"
            f"--{BOUNDARY}--\r\n"
        ).encode()
        request = FACTORY.post(
            "/", data=body, content_type=f"multipart/form-data; boundary={BOUNDARY}"
        )
        assert read(request, files()) == {}


class _Cover(forms.Form):
    cover = forms.FileField(required=False)


class TestAClearCheckbox:
    """An optional file field's clear checkbox, as a ``FormValidator`` declares it."""

    def test_it_is_a_boolean_spelt_as_every_flat_boolean_is(self) -> None:
        parameters = FormValidator(_Cover).parameters()
        request = FACTORY.post("/", data={"cover-clear": "true"})
        assert read(request, parameters) == {"cover-clear": True}

    def test_a_browser_s_default_on_is_refused(self) -> None:
        # A checkbox with no value attribute posts "on", which a flat boolean
        # does not spell: the page sets value="true".
        parameters = FormValidator(_Cover).parameters()
        request = FACTORY.post("/", data={"cover-clear": "on"})
        assert refusal(request, parameters) == {"cover-clear": ["Enter true, false, 1 or 0."]}


def _merged_multipart(fields: dict[str, Any], uploads: dict[str, Any]) -> bytes:
    """One multipart body carrying ``fields`` and then ``uploads`` under the same names."""
    head = encode_multipart(BOUNDARY, fields).rsplit(f"--{BOUNDARY}--".encode(), 1)[0]
    return head + encode_multipart(BOUNDARY, uploads)


class TestJsonBody:
    def test_the_body_is_read_as_it_is(self) -> None:
        # No coerce_flat: a JSON caller's ``"5"`` for an integer is its own
        # mistake, for the shape check to name, not a flat string to parse.
        payload = {"count": "5", "ids": [1, "2"], "title": "", "extra": None}
        assert read(post_json(payload)) == payload

    def test_a_charset_parameter_still_reads_as_json(self) -> None:
        request = FACTORY.post(
            "/", data=b'{"count": 2}', content_type="application/json; charset=utf-8"
        )
        assert read(request) == {"count": 2}

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_every_method_but_get_and_head_reads_it(self, method: str) -> None:
        assert read(post_json({"count": 2}, method)) == {"count": 2}

    def test_an_empty_body_is_no_arguments(self) -> None:
        # What DRF's parser answers for a request with no content, so a
        # client of both reads one answer to a bodiless POST.
        # Set by hand: RequestFactory drops the content type of an empty body,
        # which would send this request down the form route instead.
        request = FACTORY.generic("POST", "/", CONTENT_TYPE="application/json")
        assert request.content_type == "application/json"
        assert read(request) == {}

    def test_a_body_that_is_no_json_is_refused_as_drf_s_parser_refuses_it(self) -> None:
        # DRF answers it with a ParseError, whose code is ``parse_error``; here it
        # is one refusal of the arguments, carrying the same code.
        request = FACTORY.post("/", data=b"{", content_type="application/json")
        assert coded(refusal(request)) == {NON_FIELD_ERRORS: [(NOT_JSON, "parse_error")]}

    def test_a_body_that_is_no_object_is_refused_as_a_serializer_refuses_it(self) -> None:
        assert coded(refusal(post_json([1]))) == {
            NON_FIELD_ERRORS: [("Expected an object.", "invalid")]
        }

    @pytest.mark.parametrize(
        "body",
        [
            b"{",
            b"{'count': 2}",
            b'{"title": "\xff"}',
            b'{"n": NaN}',
            pytest.param(b'{"n": ' + b"1" * 5000 + b"}", id="an-integer-of-5000-digits"),
        ],
    )
    def test_a_malformed_body_is_refused_under_non_field_errors(self, body: bytes) -> None:
        # NaN and the infinities are Python's extension, not JSON: no JSON
        # encoder produces one, and a decoder that took one would hand a
        # Validator a value coerce_flat refuses on the flat route. The
        # integer is longer than the 4300 digits Python converts from a
        # string, which the decoder raises as a bare ValueError.
        request = FACTORY.post("/", data=body, content_type="application/json")
        assert refusal(request) == {"non_field_errors": [NOT_JSON]}

    def test_a_body_nested_past_the_decoder_is_refused_under_non_field_errors(self) -> None:
        # Sized so the decoder's own refusal is the one that answers: had it
        # decoded, the list would be refused as "Expected an object." instead.
        with pytest.raises(RecursionError):
            json.loads(DEEP_JSON)
        request = FACTORY.post("/", data=DEEP_JSON, content_type="application/json")
        assert refusal(request) == {"non_field_errors": [NOT_JSON]}

    @pytest.mark.parametrize("payload", [[1, 2], "count", 3, None])
    def test_a_body_that_is_not_an_object_is_refused(self, payload: Any) -> None:
        # The shape check's own wording, so a caller reads one refusal for a
        # value that is not an object wherever it appears.
        assert refusal(post_json(payload)) == {"non_field_errors": ["Expected an object."]}

    def test_get_ignores_a_json_body(self) -> None:
        request = FACTORY.generic(
            "GET", "/?count=1", data=b'{"count": 2}', content_type="application/json"
        )
        assert read(request) == {"count": 1}


class TestUrlKwargs:
    def test_an_unconverted_path_segment_is_coerced(self) -> None:
        # ``<pk>`` without a converter arrives as a string.
        assert read(FACTORY.get("/"), url_kwargs={"pk": "4"}) == {"pk": 4}

    def test_a_converted_value_is_left_as_it_is(self) -> None:
        assert read(FACTORY.get("/"), url_kwargs={"pk": 4}) == {"pk": 4}

    @pytest.mark.parametrize(
        "request_",
        [
            FACTORY.get("/?pk=9&count=1"),
            FACTORY.post("/", data="pk=9&count=1", content_type=FORM),
            post_json({"pk": 9, "count": 1}),
        ],
        ids=["query", "form", "json"],
    )
    def test_the_route_wins_a_clash(self, request_: HttpRequest) -> None:
        # A client value must not move the route's scope: ``/notes/4/`` with
        # ``pk=9`` in the body acts on note 4 or on nothing.
        assert read(request_, url_kwargs={"pk": "4"}) == {"pk": 4, "count": 1}

    def test_a_clashing_client_value_that_does_not_parse_is_not_reported(self) -> None:
        # It was never going to be used, so refusing it would refuse a request
        # for a value the route overrides.
        assert read(FACTORY.get("/?pk=nine"), url_kwargs={"pk": "4"}) == {"pk": 4}

    def test_a_route_value_that_does_not_parse_is_refused_with_the_rest(self) -> None:
        assert refusal(FACTORY.get("/?count=x"), url_kwargs={"pk": "four"}) == {
            "pk": ["Enter a whole number."],
            "count": ["Enter a whole number."],
        }

    def test_the_route_is_coerced_beside_a_json_body(self) -> None:
        assert read(post_json({"count": "1"}), url_kwargs={"pk": "4"}) == {
            "count": "1",
            "pk": 4,
        }

    @pytest.mark.parametrize(
        "request_",
        [
            FACTORY.get("/"),
            post_json({"count": 2}),
            FACTORY.post("/", data="count=2", content_type=FORM),
        ],
        ids=["query", "json", "form"],
    )
    def test_an_undeclared_route_value_is_the_hosts_misconfiguration(
        self, request_: HttpRequest
    ) -> None:
        # Read as a client's undeclared argument, it was a 400 blaming the
        # client for the host's route; held on every path, since each merges
        # the route by its own branch.
        with pytest.raises(ImproperlyConfigured, match="The route captures org, slug,"):
            read(request_, url_kwargs={"slug": "x", "org": "acme", "pk": "4"})


class TestArrayLength:
    """A flat array is bounded as a JSON one is, and its commas never build past the bound."""

    TOO_MANY = "Ensure this field has no more than %s elements."

    def test_a_comma_list_past_the_default_maximum_is_refused_at_its_name(self) -> None:
        # The spelling that reaches round Django's DATA_UPLOAD_MAX_NUMBER_FIELDS:
        # one key, any number of values.
        query = ",".join(map(str, range(1001)))
        assert refusal(FACTORY.get(f"/?ids={query}"), CSV) == {"ids": [self.TOO_MANY % 1000]}

    def test_a_comma_list_at_the_default_maximum_is_read(self) -> None:
        query = ",".join(map(str, range(1000)))
        assert read(FACTORY.get(f"/?ids={query}"), CSV) == {"ids": list(range(1000))}

    @override_settings(SERVICE_SPECS_MAX_ARRAY_LENGTH=3)
    @pytest.mark.parametrize(
        ("query", "handed_on"),
        [
            ("ids=1,2,3,4,5,6", ["1", "2", "3", "4,5,6"]),
            ("ids=1&ids=2&ids=3&ids=4&ids=5", ["1", "2", "3", "4"]),
            ("ids=1,2&ids=3,4,5&ids=6", ["1", "2", "3", "4,5"]),
            ("ids=1,2,3", ["1", "2", "3"]),
        ],
        ids=["one-value", "repeated-keys", "mixed", "at-the-bound"],
    )
    def test_a_flat_array_is_split_no_further_than_one_past_its_maximum(
        self, monkeypatch: pytest.MonkeyPatch, query: str, handed_on: list[str]
    ) -> None:
        # Invisible to every message, which is the length's either way: what is
        # held is the list built on the way, one element past the bound and no
        # more, so a value of a million commas is never split a million times.
        # One past rather than at it, or a list over the bound would be cut to
        # one that is accepted.
        seen: list[Any] = []
        real = request_arguments_module.coerce_flat

        def spy(parameters: Parameters, raw: Any) -> dict[str, Any]:
            seen.append(raw["ids"])
            return real(parameters, raw)

        monkeypatch.setattr(request_arguments_module, "coerce_flat", spy)
        with contextlib.suppress(InvalidArguments):
            read(FACTORY.get(f"/?{query}"), CSV)
        assert seen == [handed_on]

    @override_settings(SERVICE_SPECS_MAX_ARRAY_LENGTH=None)
    def test_a_setting_of_none_splits_every_comma(self) -> None:
        assert read(FACTORY.get("/?ids=1,2,3,4"), CSV) == {"ids": [1, 2, 3, 4]}

    @override_settings(SERVICE_SPECS_MAX_ARRAY_LENGTH=3)
    def test_a_declared_maximum_bounds_the_split_rather_than_the_default(self) -> None:
        wide = Parameters.of(Parameter("ids", "array", items="integer", max_length=5))
        assert read(FACTORY.get("/?ids=1,2,3,4,5"), wide) == {"ids": [1, 2, 3, 4, 5]}
        assert refusal(FACTORY.get("/?ids=1,2,3,4,5,6"), wide) == {"ids": [self.TOO_MANY % 5]}
