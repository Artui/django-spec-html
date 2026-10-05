"""``dispatch_request``: one request, dispatched and answered as JSON."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured

from django_service_specs.adapters.dataclass.dataclass_validator import DataclassValidator
from django_service_specs.authorization.grant import Grant
from django_service_specs.http.dispatch_request import dispatch_request
from django_service_specs.services.service_conflict import ServiceConflict
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.validation.unknown_arguments import UnknownArguments
from tests.dispatch.utils import (
    OPEN,
    REFUSED_ARCHIVED,
    RENAME,
    TENANT_SEEDS,
    OwnerOnly,
    Refuse,
    Titled,
    make_user,
)
from tests.dispatch_app.models import Note
from tests.http.utils import (
    FACTORY,
    Conflicting,
    Seen,
    body,
    note_spec,
    notes_spec,
    signed_in,
    titled_spec,
)

pytestmark = pytest.mark.django_db

UNAVAILABLE = {"detail": "The acting principal is unavailable."}


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

    def test_a_service_with_nothing_to_present_is_204(self, ada: Any) -> None:
        response = dispatch_request(titled_spec(None), post(ada, {"title": "Final"}))
        assert response.status_code == 204
        assert response.content == b""

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
