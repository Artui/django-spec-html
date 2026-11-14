from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Annotated, Any

import pytest
from django import forms
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.db.models import QuerySet
from django.http import Http404
from django_service_specs import (
    DataclassPresenter,
    FieldMarking,
    FormValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from django_spec_html.views.spec_selector_view import SpecSelectorView
from tests.dispatch.utils import OPEN, Record, make_user
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

INVALID = "Select a valid choice. sideways is not one of the available choices."
TOO_LONG = "Ensure this value has at most 5 characters (it has 7)."
DIRECTION = Parameter("direction", "string", required=True, choices=("up", "down"))


class Reorder(forms.Form):
    direction = forms.ChoiceField(choices=[("up", "Up"), ("down", "Down")], required=False)
    title = forms.CharField(max_length=5, required=False)


class Row(forms.Form):
    title = forms.CharField(max_length=5)


@dataclasses.dataclass
class NoteOut:
    pk: Annotated[int, FieldMarking.handle()]
    title: Annotated[str, FieldMarking.label()]


def notes(*, direction: str) -> QuerySet[Note]:
    return Note.objects.order_by("pk" if direction == "up" else "-pk")


def form_view(**initkwargs: Any) -> Any:
    spec = ServiceSpec(service=Record(), validator=FormValidator(Reorder), permissions=OPEN)
    return SpecFormView.as_view(
        spec=spec, template_name="spec_form.html", success_url="/done/", **initkwargs
    )


def confirm_view(**initkwargs: Any) -> Any:
    spec = ServiceSpec(service=Record(), permissions=OPEN, reads=Parameters.of(DIRECTION))
    initkwargs.setdefault("route_kwargs", ("direction",))
    return SpecConfirmView.as_view(
        spec=spec, template_name="spec_confirm_view.html", success_url="/done/", **initkwargs
    )


def formset_view(**initkwargs: Any) -> Any:
    spec = ServiceSpec(
        service=Record(),
        validator=FormValidator(Row),
        permissions=OPEN,
        many=True,
        reads=Parameters.of(DIRECTION),
    )
    return SpecFormSetView.as_view(
        spec=spec, template_name="spec_formset.html", success_url="/done/", **initkwargs
    )


def selector_view(**initkwargs: Any) -> Any:
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=notes,
        presenter=DataclassPresenter(NoteOut),
        permissions=OPEN,
        reads=Parameters.of(DIRECTION),
    )
    return SpecSelectorView.as_view(spec=spec, **initkwargs)


def rows(*titles: str) -> dict[str, Any]:
    data = {
        "form-TOTAL_FORMS": str(len(titles)),
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
    }
    data.update({f"form-{index}-title": title for index, title in enumerate(titles)})
    return data


def get(view: Any, user: Any, direction: str) -> Any:
    return view(signed_in(FACTORY.get("/"), user), direction=direction)


def posted(data: Mapping[str, Any], user: Any) -> Any:
    request = signed_in(FACTORY.post("/", data=data), user)
    request._messages = CookieStorage(request)  # ty: ignore[unresolved-attribute]
    return request


def post(view: Any, user: Any, data: Mapping[str, Any], direction: str) -> Any:
    return view(posted(data, user), direction=direction)


urlpatterns: list[Any] = []


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


PAGES = {
    "form": (form_view, {"title": "Hi"}),
    "confirm": (confirm_view, {}),
    "confirm-redirect": (lambda **off: confirm_view(refusal_redirect=True, **off), {}),
    "formset": (formset_view, rows("Hi")),
}


class TestARefusedRouteCaptureIsNotFound:
    """A capture naming nothing is a 404, as a URL converter mismatch would be."""

    @pytest.mark.parametrize("page", list(PAGES))
    def test_on_get(self, ada: Any, page: str) -> None:
        with pytest.raises(Http404):
            get(PAGES[page][0](), ada, "sideways")

    @pytest.mark.parametrize("page", list(PAGES))
    def test_on_post_before_anything_runs(self, ada: Any, page: str) -> None:
        view, data = PAGES[page]
        with pytest.raises(Http404):
            post(view(), ada, data, "sideways")

    def test_on_the_selector_page(self, ada: Any) -> None:
        with pytest.raises(Http404):
            get(selector_view(), ada, "sideways")

    @pytest.mark.parametrize("page", list(PAGES))
    def test_a_capture_it_accepts_serves_the_page(self, ada: Any, page: str) -> None:
        assert get(PAGES[page][0](), ada, "up").status_code == 200

    def test_a_capture_the_selector_accepts_serves_the_page(self, ada: Any) -> None:
        response = get(selector_view(), ada, "down")
        assert response.status_code == 200


class TestARefusedPostedArgumentIsUnchanged:
    """Only the route's refusal moved: a refused post answers as it did, either way."""

    @pytest.mark.parametrize("not_found", [True, False])
    def test_on_the_form_page(self, ada: Any, not_found: bool) -> None:
        response = post(form_view(route_refusal_404=not_found), ada, {"title": "toolong"}, "up")
        errors = response.context_data["form"].errors
        assert (response.status_code, errors) == (400, {"title": [TOO_LONG]})

    @pytest.mark.parametrize("not_found", [True, False])
    def test_on_the_formset_page(self, ada: Any, not_found: bool) -> None:
        response = post(formset_view(route_refusal_404=not_found), ada, rows("toolong"), "up")
        errors = response.context_data["formset"].errors
        assert (response.status_code, errors) == (400, [{"title": [TOO_LONG]}])


class TestTurnedOff:
    """``route_refusal_404 = False`` restores 0.12.0's answers: a 400 on GET, a refusal on POST."""

    @pytest.mark.parametrize("page", list(PAGES))
    def test_get_renders_the_page_a_choice_it_never_checked(self, ada: Any, page: str) -> None:
        assert get(PAGES[page][0](route_refusal_404=False), ada, "sideways").status_code == 200

    def test_a_form_post_is_refused_on_its_form(self, ada: Any) -> None:
        response = post(form_view(route_refusal_404=False), ada, {"title": "Hi"}, "sideways")
        errors = response.context_data["form"].errors
        assert (response.status_code, errors) == (400, {"direction": [INVALID]})

    def test_a_confirm_post_is_refused_on_its_page(self, ada: Any) -> None:
        response = post(confirm_view(route_refusal_404=False), ada, {}, "sideways")
        assert (response.status_code, response.context_data["errors"]) == (
            400,
            [f"direction: {INVALID}"],
        )

    def test_a_confirm_post_with_refusal_redirect_returns_with_the_message(self, ada: Any) -> None:
        view = confirm_view(route_refusal_404=False, refusal_redirect=True)
        request = posted({}, ada)
        response = view(request, direction="sideways")
        assert (response.status_code, response.url) == (302, "/done/")
        assert [each.message for each in get_messages(request)] == [f"direction: {INVALID}"]

    def test_a_formset_post_is_refused_on_its_formset(self, ada: Any) -> None:
        response = post(formset_view(route_refusal_404=False), ada, rows("Hi"), "sideways")
        errors = list(response.context_data["formset"].non_form_errors())
        assert (response.status_code, errors) == (400, [f"direction: {INVALID}"])

    def test_the_selector_page_is_refused_at_400(self, ada: Any) -> None:
        response = get(selector_view(route_refusal_404=False), ada, "sideways")
        assert (response.status_code, response.context_data["errors"]) == (
            400,
            [f"direction: {INVALID}"],
        )
