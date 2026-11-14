from __future__ import annotations

import datetime
import json
from typing import Any

import django
import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.urls import path
from django_service_specs import DispatchResult, FormValidator, ServiceSpec

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_view import SpecFormView
from tests.dispatch.utils import OPEN, Record, make_user
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

HTMX = {"HTTP_HX_REQUEST": "true"}
VARY = "HX-Request, HX-Boosted, HX-History-Restore-Request"


class TitleForm(forms.Form):
    title = forms.CharField()


def save_spec(returns: Any = 7) -> ServiceSpec:
    return ServiceSpec(
        service=Record(returns), permissions=OPEN, validator=FormValidator(TitleForm)
    )


def page(base: type = SpecFormView, **attributes: Any) -> type:
    attributes = {
        "template_name": "page.html",
        "fragment_template_name": "fragment.html",
        "success_url": "/done/",
        **attributes,
    }
    return type("Saves", (base,), attributes)


def posted(cls: type, headers: Any = HTMX, spec: ServiceSpec | None = None) -> Any:
    request = signed_in(FACTORY.post("/", data={"title": "Hi"}, **headers), make_user("ada"))
    response = cls.as_view(spec=save_spec() if spec is None else spec)(request)
    return response.render() if hasattr(response, "render") else response


def hx_headers(response: Any) -> dict[str, str]:
    return {name: value for name, value in response.headers.items() if name.startswith("HX-")}


def templates_with(settings: Any, *, result: bool) -> None:
    form = '<form hx-post="">{{ form }}</form>'
    shown = "<p>Saved {{ result.value }}.</p>"
    if not result:
        found = {"fragment.html": form}
    elif django.VERSION >= (6, 0):
        found = {
            "fragment.html": (f"{form}{{% partialdef spec-result %}}{shown}{{% endpartialdef %}}")
        }
    else:
        found = {"fragment.html": form, "fragment.html#spec-result": shown}
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "loaders": [("django.template.loaders.locmem.Loader", {"page.html": "", **found})]
            },
        }
    ]


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def result_fragment(settings: Any) -> None:
    templates_with(settings, result=True)


@pytest.fixture
def no_result_fragment(settings: Any) -> None:
    templates_with(settings, result=False)


@pytest.mark.usefixtures("result_fragment")
class TestTheTrigger:
    def test_the_default_answer_is_the_one_0_12_0_sent(self) -> None:
        response = posted(page())
        assert (response.status_code, response.content) == (200, b"<p>Saved 7.</p>")
        assert dict(response.headers) == {
            "Content-Type": "text/html; charset=utf-8",
            "HX-Trigger": "spec-succeeded",
            "Vary": VARY,
        }

    def test_a_name_is_sent_as_it_is(self) -> None:
        assert hx_headers(posted(page(success_trigger="saved"))) == {"HX-Trigger": "saved"}

    def test_a_mapping_is_sent_as_the_json_htmx_reads(self) -> None:
        trigger = {"saved": {"title": "Café", "on": datetime.date(2026, 11, 14)}, "closed": None}
        sent = posted(page(success_trigger=trigger))["HX-Trigger"]
        assert sent.startswith("{")
        assert sent.isascii()
        assert json.loads(sent) == {"saved": {"title": "Café", "on": "2026-11-14"}, "closed": None}

    def test_none_sends_no_trigger(self) -> None:
        response = posted(page(success_trigger=None))
        assert (response.status_code, hx_headers(response)) == (200, {})
        assert response.content == b"<p>Saved 7.</p>"

    @pytest.mark.parametrize(
        ("timing", "header"),
        [
            ("receive", "HX-Trigger"),
            ("swap", "HX-Trigger-After-Swap"),
            ("settle", "HX-Trigger-After-Settle"),
        ],
    )
    def test_each_timing_names_its_header(self, timing: str, header: str) -> None:
        response = posted(page(success_trigger="saved", success_trigger_timing=timing))
        assert hx_headers(response) == {header: "saved"}

    def test_get_success_trigger_reads_the_result(self) -> None:
        class Named(page()):
            def get_success_trigger(self, result: DispatchResult) -> Any:
                return {"saved": {"id": result.value}}

        assert json.loads(posted(Named)["HX-Trigger"]) == {"saved": {"id": 7}}

    def test_a_payload_json_cannot_carry_is_refused_when_sent(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="Saves's success trigger .* JSON"):
            posted(page(success_trigger={"saved": object()}))

    def test_a_confirmation_sends_its_trigger_too(self) -> None:
        spec = ServiceSpec(service=Record(7), permissions=OPEN)
        response = posted(page(SpecConfirmView, success_trigger="confirmed"), spec=spec)
        assert hx_headers(response) == {"HX-Trigger": "confirmed"}

    def test_a_post_without_htmx_still_redirects_alone(self) -> None:
        cls = page(success_trigger={"saved": 1}, success_trigger_timing="swap")
        response = posted(cls, headers={})
        assert (response.status_code, response["Location"], hx_headers(response)) == (
            302,
            "/done/",
            {},
        )


class TestTheRefresh:
    @pytest.mark.usefixtures("result_fragment")
    def test_a_post_without_htmx_still_redirects(self) -> None:
        response = posted(page(success_refresh=True), headers={})
        assert (response.status_code, hx_headers(response)) == (302, {})

    @pytest.mark.usefixtures("no_result_fragment")
    def test_a_refresh_needs_no_fragment(self) -> None:
        response = posted(page(success_refresh=True, success_trigger=None))
        assert (response.status_code, response.content) == (200, b"")
        assert hx_headers(response) == {"HX-Refresh": "true"}
        assert response["Vary"] == VARY

    @pytest.mark.usefixtures("result_fragment")
    def test_a_refresh_carries_the_trigger_htmx_fires_before_it_reloads(self) -> None:
        response = posted(page(success_refresh=True))
        assert response.content == b""
        assert hx_headers(response) == {"HX-Trigger": "spec-succeeded", "HX-Refresh": "true"}


@pytest.mark.usefixtures("result_fragment")
class TestCheckedAtAsView:
    @pytest.mark.parametrize(
        ("attributes", "message"),
        [
            ({"success_trigger": ""}, "success_trigger must be"),
            ({"success_trigger": 3}, "success_trigger must be"),
            ({"success_trigger": {}}, "success_trigger must be"),
            ({"success_trigger": {"": 1}}, "success_trigger must be"),
            ({"success_trigger": {1: 1}}, "success_trigger must be"),
            ({"success_trigger_timing": "load"}, "success_trigger_timing must be"),
            ({"success_refresh": 1}, "success_refresh must be"),
            ({"success_refresh": True, "success_trigger_timing": "swap"}, "never fires"),
            ({"success_refresh": True, "success_trigger_timing": "settle"}, "never fires"),
        ],
    )
    def test_a_value_the_page_cannot_send_is_refused(
        self, attributes: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ImproperlyConfigured, match=message):
            page(**attributes).as_view(spec=save_spec())

    def test_initkwargs_are_checked_too(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="success_trigger_timing must be"):
            page().as_view(spec=save_spec(), success_trigger_timing="load")

    def test_a_refresh_without_a_trigger_takes_any_timing(self) -> None:
        page(success_refresh=True, success_trigger=None, success_trigger_timing="swap").as_view(
            spec=save_spec()
        )
