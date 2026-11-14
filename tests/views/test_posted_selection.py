from __future__ import annotations

from typing import Any

import pytest
from django.contrib import messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.urls import path
from django_service_specs import Parameter, Parameters, ServiceSpec

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from tests.dispatch.utils import OPEN, Record, make_user
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, signed_in
from tests.views.test_spec_confirm_view import ONE_NOTE, PAGE

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

HTMX = {"HTTP_HX_REQUEST": "true"}
IDS = Parameter("ids", "array", items="integer")
REASON = Parameter("reason", "string")


class Ticked(SpecConfirmView):
    """A bulk action's button, the ticked rows' checkboxes posted beside it by ``hx-include``."""

    template_name = PAGE
    success_url = "/done/"
    posted_arguments = ("ids",)


def bulk_spec(*reads: Parameter) -> ServiceSpec:
    return ServiceSpec(service=Record(), permissions=OPEN, reads=Parameters.of(*reads))


def post(view: Any, data: Any, **extra: Any) -> Any:
    request = signed_in(FACTORY.post("/", data=data, **extra), make_user("ada"))
    request._messages = CookieStorage(request)  # ty: ignore[unresolved-attribute]
    return view(request), request


def pools(spec: ServiceSpec) -> list[dict[str, Any]]:
    return spec.service.calls


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


class TestTheTickedRowsArePosted:
    def test_the_ticked_ids_reach_the_service(self) -> None:
        spec = bulk_spec(IDS)
        response, _ = post(Ticked.as_view(spec=spec), {"ids": ["3", "5"]}, **HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/done/")
        assert pools(spec)[0]["ids"] == [3, 5]

    def test_a_comma_list_is_read_as_the_flat_reader_reads_it(self) -> None:
        spec = bulk_spec(IDS)
        post(Ticked.as_view(spec=spec), {"ids": "3, 5"})
        assert pools(spec)[0]["ids"] == [3, 5]

    def test_it_is_set_through_as_view_too(self) -> None:
        spec = bulk_spec(IDS)
        view = SpecConfirmView.as_view(
            spec=spec, template_name=PAGE, success_url="/done/", posted_arguments=("ids",)
        )
        post(view, {"ids": ["7"]})
        assert pools(spec)[0]["ids"] == [7]

    def test_none_ticked_is_absent(self) -> None:
        spec = bulk_spec(IDS)
        post(Ticked.as_view(spec=spec), {})
        assert "ids" not in pools(spec)[0]

    def test_a_posted_key_not_named_never_reaches_the_service(self) -> None:
        spec = bulk_spec(IDS, REASON)
        post(Ticked.as_view(spec=spec), {"ids": ["3"], "reason": "tidy", "other": "x"})
        assert pools(spec)[0]["ids"] == [3]
        assert {"reason", "other"}.isdisjoint(pools(spec)[0])

    def test_without_posted_arguments_nothing_posted_is_read(self) -> None:
        spec = bulk_spec(IDS)
        view = SpecConfirmView.as_view(spec=spec, template_name=PAGE, success_url="/done/")
        post(view, {"ids": ["3"]})
        assert "ids" not in pools(spec)[0]

    def test_the_route_s_arguments_are_merged_beside_them(self, ada_note: Note) -> None:
        spec = ServiceSpec(
            service=Record(),
            permissions=OPEN,
            instance_selector_spec=ONE_NOTE,
            reads=Parameters.of(IDS),
        )
        view = Ticked.as_view(spec=spec, route_kwargs=("pk",))
        request = signed_in(FACTORY.post("/", data={"ids": ["2"]}), ada_note.owner)
        view(request, pk=str(ada_note.pk))
        pool = pools(spec)[0]
        assert (pool["instance"], pool["ids"]) == (ada_note, [2])


@pytest.fixture
def ada_note() -> Note:
    return Note.objects.create(owner=make_user("ada"), title="Draft")


class TestARefusedPostedValue:
    def test_a_value_coercion_refuses_answers_400(self) -> None:
        spec = bulk_spec(IDS)
        response, _ = post(Ticked.as_view(spec=spec), {"ids": ["3", "x"]})
        assert response.status_code == 400
        assert response.context_data["errors"] == ["ids.1: Enter a whole number."]
        assert pools(spec) == []

    def test_a_value_the_shape_check_refuses_answers_400(self) -> None:
        spec = bulk_spec(Parameter("ids", "array", items="integer", min_length=2))
        response, _ = post(Ticked.as_view(spec=spec), {"ids": ["3"]})
        assert response.status_code == 400
        assert [e.split(":")[0] for e in response.context_data["errors"]] == ["ids"]
        assert pools(spec) == []

    def test_under_refusal_redirect_it_is_a_message_and_a_redirect(self) -> None:
        spec = bulk_spec(IDS)
        view = Ticked.as_view(spec=spec, refusal_redirect=True)
        response, request = post(view, {"ids": ["x"]})
        assert (response.status_code, response.url) == (302, "/done/")
        sent = [(m.level, m.message) for m in messages.get_messages(request)]
        assert sent == [(messages.ERROR, "ids.0: Enter a whole number.")]


class TestAsViewChecksTheNames:
    def test_a_required_parameter_posted_is_supplied(self) -> None:
        required = Parameter("ids", "array", items="integer", required=True)
        Ticked.as_view(spec=bulk_spec(required))
        with pytest.raises(ImproperlyConfigured, match=r"requires ids, which no post"):
            Ticked.as_view(spec=bulk_spec(required), posted_arguments=())

    def test_a_name_the_spec_does_not_declare_is_refused(self) -> None:
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^Ticked.posted_arguments names pks, reason, which the spec does not declare",
        ):
            Ticked.as_view(spec=bulk_spec(IDS), posted_arguments=("reason", "ids", "pks"))

    @pytest.mark.parametrize(
        "route_kwargs", [("ids",), {"selection": "ids"}], ids=["allowed", "renamed"]
    )
    def test_a_name_the_route_fills_is_refused(self, route_kwargs: Any) -> None:
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^Ticked.posted_arguments names ids, which route_kwargs fills from the URL",
        ):
            Ticked.as_view(spec=bulk_spec(IDS), route_kwargs=route_kwargs)

    @pytest.mark.parametrize("posted", ["ids", ["ids"], ("ids", 1)], ids=["str", "list", "int"])
    def test_anything_but_a_tuple_of_names_is_refused(self, posted: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"must be a tuple of parameter names"):
            Ticked.as_view(spec=bulk_spec(IDS), posted_arguments=posted)
