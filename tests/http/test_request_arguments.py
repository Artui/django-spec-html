"""``request_arguments``: a Django request read as the arguments a JSON caller would send.

Every request here comes from Django's own ``RequestFactory``, which encodes a
query string, a form and a multipart body the way a browser does, so what is
read back is what a real client's request carries rather than a double's idea
of it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import RequestFactory
from django.test.client import encode_multipart

from django_service_specs.http.request_arguments import request_arguments
from django_service_specs.http.unsupported_media_type import UnsupportedMediaType
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.parameters.parameter import Parameter
from django_service_specs.parameters.parameters import Parameters
from tests.http.utils import DEEP_JSON

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

    def test_a_multipart_post_is_flat_and_its_files_are_not_arguments(self) -> None:
        attached = SimpleUploadedFile("notes.txt", b"A file.")
        upload = FACTORY.post("/", data={"count": "2", "ids": ["1", "2"], "notes": attached})
        assert upload.content_type == "multipart/form-data"
        assert read(upload) == {"count": 2, "ids": [1, 2]}
        assert list(upload.FILES) == ["notes"]

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
        assert read(request) == {"count": 2, "ids": [1, 2]}
        # Django fills ``request.FILES`` for POST alone, as the page says.
        assert not request.FILES

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
        assert read(request) == {"count": 2}

    def test_a_request_with_no_body_carries_no_arguments_whatever_its_label(self) -> None:
        # ``fetch(url, {method: "POST"})`` for an operation its route names.
        request = FACTORY.post("/", data="", content_type="text/plain")
        assert read(request, url_kwargs={"pk": "4"}) == {"pk": 4}

    def test_a_post_does_not_read_its_query_string(self) -> None:
        request = FACTORY.post("/?count=9", data="title=x", content_type=FORM)
        assert read(request) == {"title": "x"}


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
