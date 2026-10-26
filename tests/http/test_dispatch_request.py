"""``dispatch_request``: one request, dispatched and answered as JSON."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import django_filters
import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassValidator,
    Grant,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceConflict,
    ServiceSpec,
    UnknownArguments,
)

from django_spec_html.http.dispatch_request import dispatch_request
from tests.dispatch.utils import (
    OPEN,
    REFUSED_ARCHIVED,
    RENAME,
    TENANT_SEEDS,
    Listed,
    OwnerOnly,
    Refuse,
    Titled,
    make_user,
)
from tests.dispatch_app.models import Note
from tests.http.utils import (
    FACTORY,
    HANDED_STAGES,
    SPEC_OR_SOURCE,
    Conflicting,
    Foreign,
    Handed,
    Seen,
    asking_for_tenant_spec,
    asking_spec,
    body,
    handed_spec,
    note_spec,
    notes_spec,
    signed_in,
    titled_spec,
)

pytestmark = pytest.mark.django_db

UNAVAILABLE = {"detail": "The acting principal is unavailable."}


class NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
    """``?pks=1,2``: a CSV filter takes its element type from its second base."""


class NumberCsvRangeFilter(django_filters.BaseRangeFilter, django_filters.NumberFilter):
    """``?pk_between=1,5``: two values in one argument, unlike ``RangeFilter``."""


class NoteFilters(django_filters.FilterSet):
    """The two comma-separated spellings, as real django-filter classes rather than stand-ins.

    The reading is checked against what django-filter itself reads.
    """

    pks = NumberInFilter(field_name="pk", lookup_expr="in")
    pk_between = NumberCsvRangeFilter(field_name="pk", lookup_expr="range")


@dataclasses.dataclass
class Measured:
    ratio: float


def _taken(*, title: str) -> None:
    raise ServiceConflict(f"{title!r} is taken.")


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


def post(user: Any, payload: Any, path: str = "/") -> Any:
    return signed_in(
        FACTORY.post(path, data=json.dumps(payload), content_type="application/json"), user
    )


class TestSuccess:
    def test_a_list_selector_answers_its_presented_rows(self, ada: Any) -> None:
        Note.objects.create(owner=ada, title="One")
        Note.objects.create(owner=ada, title="Two")
        response = dispatch_request(notes_spec(), signed_in(FACTORY.get("/"), ada))
        assert response.status_code == 200
        assert body(response) == [{"title": "One"}, {"title": "Two"}]

    def test_a_csv_filter_reads_the_comma_separated_spelling_its_help_advertises(
        self, ada: Any
    ) -> None:
        # ``?pks=1,2`` is what django-filter's help text promises for a CSV
        # filter, so it must narrow the rows over the wire and not only as
        # the list a JSON caller sends.
        keys = [Note.objects.create(owner=ada, title=name).pk for name in ("One", "Two", "Three")]
        spec = notes_spec(filter_set=NoteFilters)

        response = dispatch_request(spec, signed_in(FACTORY.get(f"/?pks={keys[0]},{keys[2]}"), ada))
        assert response.status_code == 200
        assert body(response) == [{"title": "One"}, {"title": "Three"}]

        between = dispatch_request(
            spec, signed_in(FACTORY.get(f"/?pk_between={keys[1]}, {keys[2]}"), ada)
        )
        assert body(between) == [{"title": "Two"}, {"title": "Three"}]

        refused = dispatch_request(spec, signed_in(FACTORY.get(f"/?pks={keys[0]},,x"), ada))
        assert refused.status_code == 400
        assert body(refused) == {"pks": {"1": ["Enter a number."], "2": ["Enter a number."]}}

    def test_a_csv_filter_refuses_a_list_past_the_default_maximum_as_the_client_s_mistake(
        self, ada: Any
    ) -> None:
        # One key and a comma per value is the spelling that passes Django's
        # DATA_UPLOAD_MAX_NUMBER_FIELDS; past the database's variable limit an
        # ``in`` lookup answered a 500. A 400 at the filter's name instead.
        spec = notes_spec(filter_set=NoteFilters)
        at_the_bound = ",".join(map(str, range(1, 1001)))
        past_it = f"{at_the_bound},1001"

        assert (
            dispatch_request(spec, signed_in(FACTORY.get(f"/?pks={at_the_bound}"), ada)).status_code
            == 200
        )
        refused = dispatch_request(spec, signed_in(FACTORY.get(f"/?pks={past_it}"), ada))
        assert refused.status_code == 400
        assert body(refused) == {"pks": ["Ensure this field has no more than 1000 elements."]}

    def test_each_presented_row_carries_its_affordances(self, ada: Any) -> None:
        Note.objects.create(owner=ada, title="One")
        Note.objects.create(owner=ada, title="Two", archived=True)

        response = dispatch_request(
            notes_spec(affordances={"rename": RENAME}), signed_in(FACTORY.get("/"), ada)
        )

        assert body(response) == [
            {"title": "One", "affordances": {"rename": {"available": True}}},
            {"title": "Two", "affordances": {"rename": REFUSED_ARCHIVED}},
        ]

    def test_a_retrieve_reads_its_pk_from_the_route(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="One")
        response = dispatch_request(
            note_spec(), signed_in(FACTORY.get("/"), ada), url_kwargs={"pk": str(note.pk)}
        )
        assert (response.status_code, body(response)) == (200, {"title": "One"})

    def test_a_missing_row_is_404(self, ada: Any) -> None:
        response = dispatch_request(note_spec(), signed_in(FACTORY.get("/?pk=404"), ada))
        assert (response.status_code, body(response)) == (404, {"detail": "Not found."})

    def test_a_service_answers_its_presented_value_bare(self, ada: Any) -> None:
        response = dispatch_request(titled_spec({"id": 7}), post(ada, {"title": " Final "}))
        assert (response.status_code, body(response)) == (200, {"id": 7})

    @SPEC_OR_SOURCE
    def test_a_service_with_nothing_to_present_is_204(self, ada: Any, served: Any) -> None:
        # Read off a source itself, a service would not be one and would
        # answer ``null`` at 200.
        response = dispatch_request(served(titled_spec(None)), post(ada, {"title": "Final"}))
        assert (response.status_code, response.content) == (204, b"")

    def test_a_selector_that_allows_none_answers_null_at_200(self, ada: Any) -> None:
        # Holds ``isinstance(spec, ServiceSpec)`` in the 204 rule: a read that
        # found nothing it was allowed to find answers ``null``, which is its
        # value, where a write with nothing to say has no body at all.
        response = dispatch_request(
            note_spec(allow_none=True), signed_in(FACTORY.get("/?pk=404"), ada)
        )
        assert (response.status_code, body(response)) == (200, None)

    def test_a_service_with_a_body_is_200(self, ada: Any) -> None:
        # Holds ``body is None`` in the 204 rule: an empty list is a body.
        response = dispatch_request(titled_spec([]), post(ada, {"title": "Final"}))
        assert (response.status_code, body(response)) == (200, [])

    def test_the_callers_success_status_wins(self, ada: Any) -> None:
        response = dispatch_request(
            titled_spec({"id": 7}), post(ada, {"title": "x"}), success_status=201
        )
        assert (response.status_code, body(response)) == (201, {"id": 7})

    def test_a_callers_success_status_holds_for_nothing_to_present(self, ada: Any) -> None:
        # An empty body with nothing to describe it, rather than ``null``.
        response = dispatch_request(
            titled_spec(None), post(ada, {"title": "x"}), success_status=200
        )
        assert (response.status_code, response.content) == (200, b"")
        assert "Content-Type" not in response

    def test_a_204_carries_no_body_whatever_was_presented(self, ada: Any) -> None:
        response = dispatch_request(
            titled_spec({"id": 7}), post(ada, {"title": "x"}), success_status=204
        )
        assert (response.status_code, response.content) == (204, b"")


class TestPrincipal:
    def test_anonymous_reaches_the_permission_check(self) -> None:
        # Holds ``is_authenticated`` in the deactivated guard: AnonymousUser's
        # ``is_active`` is False, so without it every anonymous request would
        # be refused before a check that admits anonymous could say so.
        seen = Seen()
        response = dispatch_request(
            titled_spec({"id": 1}, permissions=[seen]), post(None, {"title": "x"})
        )
        assert response.status_code == 200
        assert len(seen.principals) == 1
        assert isinstance(seen.principals[0], AnonymousUser)

    def test_the_request_user_is_the_principal(self, ada: Any) -> None:
        # Holds ``not is_active`` in the deactivated guard: an active user acts.
        seen = Seen()
        dispatch_request(titled_spec({"id": 1}, permissions=[seen]), post(ada, {"title": "x"}))
        assert seen.principals == [ada]

    def test_a_deactivated_user_is_refused_before_anything_runs(self, ada: Any) -> None:
        # A backend other than ModelBackend hands one through; the kernel's
        # rule is that a deactivated principal never acts.
        ada.is_active = False
        seen = Seen()
        spec = titled_spec({"id": 1}, permissions=[seen])
        response = dispatch_request(spec, post(ada, {"title": "x"}))
        assert (response.status_code, body(response)) == (403, UNAVAILABLE)
        assert seen.principals == []
        assert spec.service.calls == []

    def test_a_deactivated_user_is_refused_ahead_of_a_malformed_body(self, ada: Any) -> None:
        # The principal is read first, as adispatch resolves one before its
        # shape check: a caller who may not act learns nothing about the call.
        ada.is_active = False
        request = signed_in(FACTORY.post("/", data=b"{", content_type="application/json"), ada)
        response = dispatch_request(titled_spec(), request)
        assert (response.status_code, body(response)) == (403, UNAVAILABLE)


class TestRefusals:
    def test_a_denial_is_403_with_the_checks_message(self, ada: Any) -> None:
        response = dispatch_request(titled_spec(permissions=[Refuse()]), post(ada, {"title": "x"}))
        assert (response.status_code, body(response)) == (
            403,
            {"detail": "Only editors may run this."},
        )

    def test_an_object_level_denial_is_403(self, ada: Any) -> None:
        note = Note.objects.create(owner=make_user("bob"), title="Theirs")
        response = dispatch_request(
            note_spec(permissions=[OwnerOnly()]), signed_in(FACTORY.get(f"/?pk={note.pk}"), ada)
        )
        assert (response.status_code, body(response)) == (
            403,
            {"detail": "Only the owner may touch this note."},
        )

    def test_an_invalid_argument_set_is_400_with_its_tree(self, ada: Any) -> None:
        response = dispatch_request(titled_spec(), post(ada, {"colour": "red"}))
        assert (response.status_code, body(response)) == (
            400,
            {"title": ["This field is required."], "colour": ["Unknown argument."]},
        )

    def test_a_number_the_decoder_reads_as_infinity_is_400_at_its_address(self, ada: Any) -> None:
        # 1e400 is well-formed JSON, and Python's decoder reads it as infinity:
        # the constants are refused by name, and this is refused as a number.
        spec = ServiceSpec(
            service=lambda **pool: None, permissions=OPEN, validator=DataclassValidator(Measured)
        )
        request = FACTORY.post("/", data=b'{"ratio": 1e400}', content_type="application/json")
        response = dispatch_request(spec, signed_in(request, ada))
        assert (response.status_code, body(response)) == (400, {"ratio": ["Enter a number."]})

    def test_a_malformed_body_is_400(self, ada: Any) -> None:
        request = signed_in(FACTORY.post("/", data=b"[1", content_type="application/json"), ada)
        response = dispatch_request(titled_spec(), request)
        assert (response.status_code, body(response)) == (
            400,
            {"non_field_errors": ["The request body is not valid JSON."]},
        )

    def test_the_csrf_token_is_not_an_unknown_argument(self, ada: Any) -> None:
        request = signed_in(
            FACTORY.post("/", data={"csrfmiddlewaretoken": "t0k3n", "title": "x"}), ada
        )
        response = dispatch_request(titled_spec({"id": 1}), request)
        assert (response.status_code, body(response)) == (200, {"id": 1})

    def test_a_services_own_refusal_is_answered(self, ada: Any) -> None:
        spec = ServiceSpec(service=_taken, permissions=OPEN, validator=Titled())
        response = dispatch_request(spec, post(ada, {"title": "Final"}))
        assert (response.status_code, body(response)) == (409, {"detail": "'Final' is taken."})

    def test_a_refusal_while_presenting_is_answered(self, ada: Any) -> None:
        response = dispatch_request(
            titled_spec({"id": 1}, presenter=Conflicting()), post(ada, {"title": "x"})
        )
        assert (response.status_code, body(response)) == (
            409,
            {"detail": "Moved while you were reading."},
        )

    def test_a_configuration_error_is_not_answered(self, ada: Any) -> None:
        # Wrong for every caller, so it propagates to the host's 500 rather
        # than reading to one client as something about its request.
        with pytest.raises(ImproperlyConfigured):
            dispatch_request(titled_spec(permissions=None), post(ada, {"title": "x"}))


class TestPassedThrough:
    def test_a_grant_stands_in_for_the_class_level_check(self, ada: Any) -> None:
        spec = titled_spec({"id": 1}, permissions=[Refuse()])
        response = dispatch_request(spec, post(ada, {"title": "x"}), grant=Grant(spec, ada))
        assert response.status_code == 200

    def test_the_pool_seeds_reach_the_service(self, ada: Any) -> None:
        spec = titled_spec({"id": 1})
        dispatch_request(spec, post(ada, {"title": "x"}), pool_seeds=TENANT_SEEDS)
        assert spec.service.calls[0]["tenant"] == "tenant-of-ada"

    def test_the_unknown_argument_policy_is_the_callers(self, ada: Any) -> None:
        response = dispatch_request(
            titled_spec({"id": 1}),
            post(ada, {"title": "x", "colour": "red"}),
            unknown_arguments=UnknownArguments.IGNORE,
        )
        assert response.status_code == 200

    def test_the_route_is_merged_last(self, ada: Any) -> None:
        mine = Note.objects.create(owner=ada, title="Mine")
        other = Note.objects.create(owner=ada, title="Other")
        response = dispatch_request(
            note_spec(),
            signed_in(FACTORY.get(f"/?pk={other.pk}"), ada),
            url_kwargs={"pk": mine.pk},
        )
        assert body(response) == {"title": "Mine"}

    def test_route_kwargs_rename_and_drop_the_route(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="One")
        response = dispatch_request(
            note_spec(),
            signed_in(FACTORY.get("/"), ada),
            url_kwargs={"org": "acme", "note": str(note.pk)},
            route_kwargs={"org": None, "note": "pk"},
        )
        assert (response.status_code, body(response)) == (200, {"title": "One"})

    def test_a_bound_value_outranks_the_query(self, ada: Any) -> None:
        mine = Note.objects.create(owner=ada, title="Mine")
        response = dispatch_request(
            note_spec(),
            signed_in(FACTORY.get("/?pk=abc"), ada),
            server_arguments=ServerArguments(values={"pk": mine.pk}),
        )
        assert (response.status_code, body(response)) == (200, {"title": "Mine"})

    def test_a_route_filling_a_bound_name_is_the_hosts_misconfiguration(self, ada: Any) -> None:
        with pytest.raises(
            ImproperlyConfigured, match="The route captures pk, which server_arguments"
        ):
            dispatch_request(
                note_spec(),
                signed_in(FACTORY.get("/"), ada),
                url_kwargs={"pk": "4"},
                server_arguments=ServerArguments(values={"pk": 4}),
            )

    def test_a_bound_value_its_parameter_refuses_is_the_hosts_error(self, ada: Any) -> None:
        # Not the client's 400: the client neither sent the value nor can see it.
        with pytest.raises(ImproperlyConfigured, match="pk"):
            dispatch_request(
                note_spec(),
                signed_in(FACTORY.get("/"), ada),
                server_arguments=ServerArguments(values={"pk": "four"}),
            )


class TestAskedInput:
    # The schema a service asks under is checked before it is written to the
    # body: a schema the route could not hear answered is the raiser's error.
    @SPEC_OR_SOURCE
    def test_a_schema_naming_a_declared_key_is_written_as_the_422_body(
        self, ada: Any, served: Any
    ) -> None:
        schema = {"title": {"type": "string"}}
        response = dispatch_request(served(asking_spec(schema)), post(ada, {"title": "x"}))
        assert (response.status_code, body(response)) == (
            422,
            {"detail": "Say which.", "schema": schema},
        )

    @SPEC_OR_SOURCE
    def test_a_schema_naming_an_undeclared_key_is_the_services_error_not_a_422(
        self, ada: Any, served: Any
    ) -> None:
        asking = served(asking_spec({"colour": {"type": "string"}}))
        with pytest.raises(ImproperlyConfigured, match="colour"):
            dispatch_request(asking, post(ada, {"title": "x"}))

    # Two cases size the seed rule to be the one that answers: ``tenant`` is
    # declared, so the "no parameter declares it" rule has nothing to say, and
    # it is a well-formed string question, so no shape rule does either. Only
    # the request's seeds differ. ``check_asked_input`` reading ``pool_seeds``
    # is held by the first; the second shows the seed, not the key, is what
    # refuses.
    @SPEC_OR_SOURCE
    def test_a_schema_naming_a_key_the_requests_seeds_fill_is_the_services_error(
        self, ada: Any, served: Any
    ) -> None:
        asking = served(asking_for_tenant_spec({"tenant": {"type": "string"}}))
        with pytest.raises(
            ImproperlyConfigured, match="a provider or a pool seed fills it"
        ) as refused:
            dispatch_request(asking, post(ada, {"title": "x"}), pool_seeds=TENANT_SEEDS)
        assert "declares no top-level parameter" not in str(refused.value)

    @SPEC_OR_SOURCE
    def test_the_same_schema_without_that_seed_is_written_as_the_422_body(
        self, ada: Any, served: Any
    ) -> None:
        schema = {"tenant": {"type": "string"}}
        response = dispatch_request(
            served(asking_for_tenant_spec(schema)), post(ada, {"title": "x"})
        )
        assert (response.status_code, body(response)) == (
            422,
            {"detail": "Say which.", "schema": schema},
        )


@dataclasses.dataclass
class Asked:
    """A Validator field named like the call's ``request``."""

    request: str


class TestTheRequestAndTheView:
    """The call's context carries the request, and the view a host passes, to every stage."""

    def test_every_stage_is_handed_the_request_and_no_view(self, ada: Any) -> None:
        handed = Handed()
        note = Note.objects.create(owner=ada, title="Ada's")
        request = post(ada, {"title": "Hi"})
        response = dispatch_request(handed_spec(handed), request, url_kwargs={"pk": note.pk})
        assert response.status_code == 204
        assert handed.stages() == HANDED_STAGES
        assert handed.one_view(request) is None

    def test_the_view_passed_is_the_one_every_stage_is_handed(self, ada: Any) -> None:
        handed = Handed()
        note = Note.objects.create(owner=ada, title="Ada's")
        request = post(ada, {"title": "Hi"})
        view = object()
        response = dispatch_request(
            handed_spec(handed), request, url_kwargs={"pk": note.pk}, view=view
        )
        assert response.status_code == 204
        assert handed.stages() == HANDED_STAGES
        assert handed.one_view(request) is view

    def test_a_seed_resolver_reading_the_request_binds(self, ada: Any) -> None:
        spec = titled_spec({"id": 1})
        request = post(ada, {"title": "x"})
        seeds = DEFAULT_POOL_SEEDS.extend(asker=lambda *, request: request)
        assert dispatch_request(spec, request, pool_seeds=seeds).status_code == 200
        assert spec.service.calls[0]["asker"] is request

    def test_a_parameter_named_request_is_refused(self, ada: Any) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda **pool: Note.objects.none(),
            reads=Parameters.of(Parameter("request", "string")),
            permissions=OPEN,
        )
        with pytest.raises(ImproperlyConfigured, match="request"):
            dispatch_request(spec, signed_in(FACTORY.get("/?request=x"), ada))

    def test_a_field_named_request_is_read_from_data_beside_the_call_s(self, ada: Any) -> None:
        seen: list[tuple[Any, Any]] = []

        def service(*, data: Asked, request: Any) -> None:
            seen.append((data.request, request))

        spec = ServiceSpec(service=service, permissions=OPEN, validator=DataclassValidator(Asked))
        request = post(ada, {"request": "submitted"})
        assert dispatch_request(spec, request).status_code == 204
        assert seen == [("submitted", request)]
        assert seen[0][1] is request


class TestSources:
    # The shape of a host serving another library's declarations: the object
    # it holds is handed in as it is.
    def test_a_list_source_answers_its_presented_rows(self, ada: Any) -> None:
        Note.objects.create(owner=ada, title="One")
        response = dispatch_request(Foreign(notes_spec()), signed_in(FACTORY.get("/"), ada))
        assert (response.status_code, body(response)) == (200, [{"title": "One"}])


def test_a_list_level_refusal_beside_a_row_s_is_one_400_body(ada: Any) -> None:
    """A many call's list node holds row indices beside ``non_field_errors``,
    and the body carries both, rows first, as the refusal tree orders them."""
    spec = ServiceSpec(service=print, permissions=OPEN, many=True, validator=Listed(most=1))
    payload = {"items": [{"title": 3}, {"title": "a"}, {"title": "b"}]}

    response = dispatch_request(spec, post(ada, payload))

    assert response.status_code == 400
    assert list(body(response)["items"]) == ["0", "non_field_errors"]
    assert body(response)["items"]["non_field_errors"] == ["At most 1 rows."]
